"""Telegram updates -> existing Store domain records + durable reply queue."""
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
import json
import logging
import sqlite3
from store import DataError, ident
from postupashki_data import utc

log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def unix_time(value):
    if type(value) is not int:
        raise DataError('Expected integer Telegram event time')
    return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec='microseconds')


def member(value):
    return value['status'] in ('member','administrator','creator') or (
        value['status']=='restricted' and value.get('is_member',False))


def initialize(store, ds):
    store.bind(ds)


class Collector:
    def __init__(self, store, config, bot_id):
        self.s,self.c,self.bot = store,config,bot_id
        self.ds = config.dataset
        initialize(store,self.ds)
        with store.db:
            previous = store.db.execute('SELECT dataset_id FROM bot_runtime WHERE bot_id=?',(bot_id,)).fetchone()
            if previous and previous[0] != self.ds:
                raise DataError('This bot is already bound to another dataset in this database')
            store.db.execute('INSERT OR IGNORE INTO bot_runtime(bot_id,dataset_id) VALUES (?,?)',(bot_id,self.ds))

    def ingest(self, update, received_at=None, retry=False):
        uid = update.get('update_id')
        if type(uid) is not int:
            raise DataError('Telegram update_id is required')
        encoded = json.dumps(update,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        with self.s.db:
            previous = self.s.db.execute('SELECT * FROM bot_updates WHERE bot_id=? AND update_id=?',(self.bot,uid)).fetchone()
            if previous:
                if previous['dataset_id'] != self.ds or previous['payload_json'] != encoded:
                    raise DataError('Conflicting Telegram update ID; original update preserved')
                if previous['status'] == 'processed' or (previous['status'] == 'failed' and not retry):
                    return previous['status']
                received_at = previous['received_at']
            else:
                received_at = utc(received_at or now())
                self.s.db.execute('INSERT INTO bot_updates(bot_id,update_id,dataset_id,received_at,payload_json) VALUES (?,?,?,?,?)',
                                  (self.bot,uid,self.ds,received_at,encoded))
        self.uid,self.received = uid,received_at
        self.root = f'tg:{self.bot}:{uid}'
        self.actions = []
        try:
            with self.s.db:
                self.s.db.execute("BEGIN IMMEDIATE")
                self.dispatch(update)
                self.s.validate()
                for i,(method,payload) in enumerate(self.actions):
                    self.s.db.execute('INSERT INTO bot_outbox(bot_id,update_id,seq,method,payload_json) VALUES (?,?,?,?,?)',
                                      (self.bot,uid,i,method,json.dumps(payload,ensure_ascii=False)))
                self.s.db.execute("UPDATE bot_updates SET status='processed',error=NULL WHERE bot_id=? AND update_id=?",(self.bot,uid))
            return 'processed'
        except (DataError,KeyError,TypeError,ValueError,OverflowError,sqlite3.IntegrityError) as e:
            with self.s.db:
                self.s.db.execute("UPDATE bot_updates SET status='failed',error=? WHERE bot_id=? AND update_id=?",
                                  (str(e)[:1000],self.bot,uid))
            log.error('Update %s/%s was saved for review: %s',self.bot,uid,type(e).__name__)
            return 'failed'

    def record(self, value):
        if value['type'] != 'lead':
            raise DataError('Unsupported legacy record type')
        uid=int(value['user_key'].split(':')[-1])
        self.s.user(uid,value['created_at'],self.received)
        return self.s.add('leads',lead_id=value['id'],user_id=uid,interest=value['interest'],
                          created_at=value['created_at'],recorded_at=self.received)

    def touch(self, kind, user, timestamp, suffix=None, **fields):
        at=utc(timestamp);eid=self.root+':'+(suffix or kind)
        self.s.user(user,at,self.received)
        base=dict(event_id=eid,user_id=user,occurred_at=at,recorded_at=self.received)
        meta=fields.get('metadata',{})
        token=meta.get('token') or meta.get('invite_link')
        link=self.s.db.execute('SELECT * FROM tracking_links WHERE source_token=?',(token,)).fetchone() if token else None
        pid=fields.get('placement_id')
        # Only a route resolved for this command/target chat can supply a source.
        # Keep membership itself even when an invite predates the registered ad.
        valid_link=link and meta.get('source_reason') in ('mapped_token','mapped_invite')
        if valid_link and link['created_at']>at:
            valid_link=False;meta['source_reason']='before_tracking_link'
        if valid_link and link['placement_id']:
            placement=self.s.db.execute('SELECT published_at FROM placements WHERE placement_id=?',(link['placement_id'],)).fetchone()
            if placement['published_at']>at:
                valid_link=False;meta['source_reason']='before_placement_publication'
        src=link['source_channel_id'] if valid_link else None
        if kind in ('bot_start','channel_join_unique_invite','channel_join_unknown_source'):
            known=src is not None
            self.s.add('acquisition_events',**base,source_channel_id=src,placement_id=pid if known else None,
                       mechanism=('bot_start' if kind=='bot_start' else 'invite_link') if known else 'unknown',
                       source_token=token if known else None)
            self.s.add('acquisition_notes',event_id=eid,reason=meta.get('source_reason','no_source'),
                       time_source=meta.get('time_source','telegram'),recorded_at=self.received)
            if kind=='bot_start':
                self.s.add('activity_events',**base,event_type='bot_started')
        if kind in ('channel_join_unique_invite','channel_join_unknown_source','channel_leave'):
            channel=self.s.channel(fields['chat_id'])['channel_id']
            status='joined' if kind!='channel_leave' else 'banned' if meta.get('new_status')=='kicked' else 'left'
            self.s.add('membership_events',**base,channel_id=channel,status=status,evidence_kind='status_update')
        elif kind=='manual_source':
            source=self.s.resolve_token(self.ds,meta.get('answer',''))
            if source:src,pid=source['source_channel_id'],source['placement_id']
            if src is None:
                r=self.s.db.execute('SELECT channel_id FROM channels WHERE title=? OR channel_id=?',(meta.get('answer',''),meta.get('answer',''))).fetchall()
                if len(r)==1:src=r[0][0]
            self.s.add('manual_sources',**base,source_channel_id=src,placement_id=pid,answer=meta.get('answer',''))
        elif kind in ('event_registered','event_submission'):
            self.s.add('participation_events',**base,hackathon_id=fields['activity_id'],
                       status='registered' if kind=='event_registered' else 'solution_submitted')
            if kind=='event_submission':
                self.s.add('submission_links',event_id=eid,url=meta['url'],recorded_at=self.received)
        elif kind=='event_completed':
            self.s.add('completion_events',**base,hackathon_id=fields['activity_id'],confirmed_by=meta['confirmed_by'])
        elif kind=='channel_join_request':
            self.s.add('join_requests',**base,channel_id=self.s.channel(fields['chat_id'])['channel_id'])
        # event_active is derived from solution_submitted, lead_requested from leads.

    def send(self, chat, text, keyboard=None):
        data = dict(chat_id=chat,text=text)
        if keyboard:
            data['reply_markup'] = {'inline_keyboard':keyboard}
        self.actions.append(('sendMessage',data))

    def dispatch(self, update):
        if 'chat_member' in update:
            x = update['chat_member']
            chat = x['chat']['id']
            if chat not in self.c.channels:
                return
            # from.id is the actor (possibly an administrator), not the subscriber.
            user = x['new_chat_member']['user']
            if user.get('is_bot'):
                return
            was,is_now = member(x['old_chat_member']),member(x['new_chat_member'])
            if was == is_now:
                return
            at = unix_time(x['date'])
            metadata = {'old_status':x['old_chat_member']['status'],'new_status':x['new_chat_member']['status'],
                        'actor_id':x['from']['id'],'time_source':'telegram'}
            if is_now:
                link = x.get('invite_link',{}).get('invite_link')
                source = self.s.resolve_invite(self.ds,chat,link) if link else None
                metadata.update(invite_link=link,source_reason='mapped_invite' if source else 'unmapped_invite' if link else 'no_invite',
                                via_join_request=x.get('via_join_request',False),
                                via_chat_folder_invite_link=x.get('via_chat_folder_invite_link',False))
                kind = 'channel_join_unique_invite' if source else 'channel_join_unknown_source'
                self.touch(kind,user['id'],at,chat_id=chat,placement_id=source['placement_id'] if source else None,metadata=metadata)
                if link and not source:
                    log.warning('Unmapped invite for update %s in channel %s',self.uid,chat)
            else:
                self.touch('channel_leave',user['id'],at,chat_id=chat,metadata=metadata)
        elif 'chat_join_request' in update:
            x = update['chat_join_request']
            chat = x['chat']['id']
            if chat in self.c.channels and not x['from'].get('is_bot'):
                link = x.get('invite_link',{}).get('invite_link')
                source = self.s.resolve_invite(self.ds,chat,link) if link else None
                self.touch('channel_join_request',x['from']['id'],unix_time(x['date']),chat_id=chat,
                    placement_id=source['placement_id'] if source else None,metadata={'invite_link':link,'time_source':'telegram'})
        elif 'my_chat_member' in update:
            x = update['my_chat_member']
            log.info('Bot membership in chat %s: %s. Only CHANNEL_IDS are monitored.',x['chat']['id'],x['new_chat_member']['status'])
        elif 'callback_query' in update:
            self.callback(update['callback_query'])
        elif 'message' in update:
            self.message(update['message'])

    def menu(self, chat):
        keys = [[{'text':'Мероприятия','callback_data':'events'}], [{'text':'Курсы и темы','callback_data':'catalog'}], [{'text':'Моя анкета','callback_data':'profile'}]]
        if self.c.manager:
            keys.insert(0,[{'text':'Связаться с менеджером','callback_data':'manager'}])
        self.send(chat,'Добро пожаловать! Здесь можно записаться на мероприятие и оставить заявку менеджеру.\n'
            '/help — команды. /privacy — какие данные сохраняются.',keys)

    def message(self, x):
        if x['chat']['type'] != 'private' or x.get('from',{}).get('is_bot'):
            return
        user,chat = x['from']['id'],x['chat']['id']
        text = x.get('text','')
        if not text.startswith('/'):
            return
        parts = text.split(maxsplit=1)
        command = parts[0].split('@')[0].lower()
        args = parts[1].strip() if len(parts)>1 else ''
        at = unix_time(x['date'])
        if command == '/start':
            source = self.s.resolve_token(self.ds,args) if args else None
            reason = 'mapped_token' if source else 'unknown_token' if args else 'no_token'
            if source and (source['publication_us'] > self.s.stamp(self.ds,at)[1] or source['created_at']>utc(at)):
                source,reason = None,'before_placement_publication'
            self.touch('bot_start',user,at,placement_id=source['id'] if source else None,
                metadata={'token':args[:256] or None,'source_reason':reason,'time_source':'telegram'})
            self.menu(chat)
        elif command in ('/profile','/topic','/program','/price','/waitlist','/attend'):
            self.extra_command(command,args,user,chat,at)
        elif command in ('/id','/whoami'):
            self.send(chat,f'Ваш Telegram ID: {user}')
        elif command == '/help':
            self.send(chat,'/events — мероприятия\n/register КОД — регистрация\n/submit КОД ССЫЛКА — отправить работу\n'
                '/manager — заявка менеджеру\n/source ИСТОЧНИК — откуда вы узнали о нас\n/id — ваш Telegram ID\n/privacy — данные')
        elif command == '/privacy':
            self.send(chat,'Бот сохраняет ваш Telegram ID, действия в боте, регистрации и присланные ссылки на работы. '
                'В подключённых каналах сохраняются события вступления и выхода и доступная ссылка приглашения. '
                'Эти данные используются организаторами для учёта мероприятий и рекламы. '
                'Технические входящие события хранятся для восстановления после ошибок. Обратиться по поводу данных можно к организаторам.')
        elif command == '/manager':
            self.lead(user,chat,at,'telegram')
        elif command == '/events':
            self.events(chat,at)
        elif command == '/register':
            self.register(user,chat,at,args)
        elif command == '/submit':
            self.submit(user,chat,at,args)
        elif command == '/source':
            if not args:
                self.send(chat,'Напишите /source и название источника, например: /source канал о стажировках')
                return
            source = self.s.resolve_token(self.ds,args)
            self.touch('manual_source',user,at,placement_id=source['id'] if source else None,
                metadata={'answer':args[:500],'time_source':'telegram','evidence':'self_reported'})
            self.send(chat,'Спасибо, источник записан с ваших слов.')
        elif command == '/complete':
            if user not in self.c.admins:
                self.send(chat,'Эта команда доступна организаторам.')
                return
            values = args.split()
            if len(values)!=2 or not values[1].isdigit() or int(values[1])<=0:
                self.send(chat,'Формат: /complete КОД_МЕРОПРИЯТИЯ TELEGRAM_ID')
                return
            aid,target = values[0],int(values[1])
            if not self.has_event(target,aid,'event_submission'):
                self.send(chat,'Сначала участник должен зарегистрироваться и отправить работу.')
                return
            if not self.has_event(target,aid,'event_completed'):
                self.touch('event_completed',target,at,activity_id=aid,metadata={'confirmed_by':user,'time_source':'telegram'})
            self.send(chat,f'Завершение мероприятия {aid} подтверждено для {target}.')
        else:
            self.send(chat,'Неизвестная команда. /help — список команд.')

    def callback(self, x):
        msg = x.get('message',{})
        if msg.get('chat',{}).get('type') != 'private' or x['from'].get('is_bot'):
            return
        self.actions.append(('answerCallbackQuery',{'callback_query_id':x['id']}))
        user,chat = x['from']['id'],msg['chat']['id']
        if user != chat:
            return
        # CallbackQuery has no timestamp: preserve the first receive time, never message.date.
        if x.get('data') == 'manager':
            self.lead(user,chat,self.received,'received_at')
        elif x.get('data') == 'catalog':
            rows=self.s.db.execute('SELECT course_id,title FROM courses ORDER BY title LIMIT 20').fetchall()
            self.send(chat,'\n'.join(f"{r['title']}: /program {r['course_id']} · /price {r['course_id']} · /waitlist {r['course_id']}" for r in rows) or 'Каталог пока пуст.')
        elif x.get('data') == 'profile':
            self.send(chat,'Образование: /profile education_stage university (school / university / graduate / other / unknown)\n'
                'Поиск работы: /profile job_search_status internship (not_searching / exploring / internship / job / interviewing / unknown)')
        elif x.get('data','').startswith('offer:'):
            values=x['data'].split(':')
            if len(values)!=3 or values[2] not in ('clicked','declined'):return
            row=self.s.db.execute('SELECT * FROM offers WHERE offer_id=?',(values[1],)).fetchone()
            if not row or row['user_id']!=user or row['status']!='sent':
                self.send(chat,'Это предложение недоступно.');return
            self.s.add('promotion_responses',response_id=self.root+':response',offer_id=values[1],kind=values[2],
                       occurred_at=self.received,recorded_at=self.received)
            self.send(chat,'Ответ записан.')
        elif x.get('data') == 'events':
            self.events(chat,self.received)
        elif x.get('data','').startswith('register:'):
            self.register(user,chat,self.received,x['data'].split(':',1)[1],time_source='received_at')

    def lead(self, user, chat, at, time_source):
        if not self.c.manager:
            self.send(chat,'Контакт менеджера пока не настроен. Обратитесь к организатору.')
            return
        lid = self.root+':lead'
        self.record(dict(type='lead',id=lid,user_key=f'tg:{user}',interest='Заявка через Telegram',created_at=at))
        self.touch('lead_requested',user,at,metadata={'lead_id':lid,'time_source':time_source})
        url = f'https://t.me/{self.c.manager}?text='+quote(f'Здравствуйте! Интересуют курсы. Номер заявки: {lid}',safe='')
        self.send(chat,f'Заявка {lid} создана. Нажмите кнопку и отправьте сообщение менеджеру.',
                  [[{'text':'Написать менеджеру','url':url}]])

    def events(self, chat, at):
        _,us = self.s.stamp(self.ds,at)
        rows = [self.activity(r[0]) for r in self.s.db.execute(
            'SELECT hackathon_id FROM hackathons WHERE ends_at>=? AND recorded_at<=? ORDER BY starts_at LIMIT 20',
            (utc(at),utc(self.received)))]
        if not rows:
            self.send(chat,'Сейчас нет открытых мероприятий.')
        for row in rows:
            start = datetime.fromisoformat(row['starts_at']).strftime('%d.%m.%Y %H:%M')
            end = datetime.fromisoformat(row['ends_at']).strftime('%d.%m.%Y %H:%M')
            text = f"{row['title']}\nКод: {row['id']}\nНачало: {start}\nОкончание: {end}\nВремя в UTC."
            if len(('register:'+row['id']).encode()) <= 64:
                self.send(chat,text,[[{'text':'Зарегистрироваться','callback_data':'register:'+row['id']}]])
            else:
                self.send(chat,text+'\n/register '+row['id'])

    def activity(self, aid):
        try:ident(aid)
        except DataError:return None
        row=self.s.db.execute('SELECT * FROM hackathons WHERE hackathon_id=?',(aid,)).fetchone()
        if not row:return None
        r=dict(row);r['id']=r['hackathon_id']
        r['starts_us']=self.s.stamp(self.ds,r['starts_at'])[1]
        r['ends_us']=self.s.stamp(self.ds,r['ends_at'])[1]
        return r

    def has_event(self, user, aid, kind):
        if kind=='event_completed':
            return self.s.db.execute('SELECT 1 FROM completion_events WHERE user_id=? AND hackathon_id=?',(user,aid)).fetchone() is not None
        status={'event_registered':'registered','event_active':'solution_submitted','event_submission':'solution_submitted'}[kind]
        return self.s.db.execute('SELECT 1 FROM participation_events WHERE user_id=? AND hackathon_id=? AND status=?',
                                (user,aid,status)).fetchone() is not None

    def register(self, user, chat, at, aid, time_source='telegram'):
        a = self.activity(aid)
        if not a:
            self.send(chat,'Не найдено мероприятие. /events — доступные мероприятия.')
            return
        if self.s.stamp(self.ds,at)[1] > a['ends_us']:
            self.send(chat,'Регистрация на это мероприятие завершена.')
            return
        if not self.has_event(user,aid,'event_registered'):
            self.touch('event_registered',user,at,activity_id=aid,metadata={'time_source':time_source})
        self.send(chat,f"Вы зарегистрированы: {a['title']}.\nОтправить работу: /submit {aid} ССЫЛКА")

    def submit(self, user, chat, at, args):
        values = args.split(maxsplit=1)
        if len(values)!=2:
            self.send(chat,'Формат: /submit КОД_МЕРОПРИЯТИЯ https://ссылка-на-работу')
            return
        aid,url = values
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http','https') and bool(parsed.hostname) and not any(c.isspace() for c in url) and len(url)<=2000
        except ValueError:
            valid = False
        if not valid:
            self.send(chat,'Нужна ссылка http:// или https:// на вашу работу.')
            return
        a = self.activity(aid)
        if not a or not self.has_event(user,aid,'event_registered'):
            self.send(chat,'Сначала зарегистрируйтесь: /register КОД_МЕРОПРИЯТИЯ')
            return
        if not a['starts_us'] <= self.s.stamp(self.ds,at)[1] <= a['ends_us']:
            self.send(chat,'Сейчас работы на это мероприятие не принимаются.')
            return
        self.touch('event_submission',user,at,activity_id=aid,metadata={'url':url,'time_source':'telegram'})
        if not self.has_event(user,aid,'event_active'):
            self.touch('event_active',user,at,activity_id=aid,
                       metadata={'criterion':'first_submission','time_source':'telegram'})
        self.send(chat,'Работа записана. Организатор отдельно подтвердит завершение участия.')

    def extra_command(self,command,args,user,chat,at):
        values=args.split(); self.s.user(user,at,self.received)
        base=dict(event_id=self.root+':'+command[1:],user_id=user,occurred_at=at,recorded_at=self.received)
        if command=='/profile':
            if len(values)!=2:
                self.send(chat,'Формат: /profile education_stage university или /profile job_search_status internship');return
            self.s.add('profile_events',**base,field=values[0],value=values[1],source='bot_form')
        elif command=='/attend':
            if user not in self.c.admins:
                self.send(chat,'Эта команда доступна организаторам.');return
            if len(values)!=2 or not values[1].isdigit():
                self.send(chat,'Формат: /attend КОД TELEGRAM_ID');return
            target=int(values[1])
            if not self.has_event(target,values[0],'event_registered'):
                self.send(chat,'Сначала участник должен зарегистрироваться.');return
            base['user_id']=target
            self.s.add('participation_events',**base,hackathon_id=values[0],status='attended')
        else:
            if len(values)!=1:
                self.send(chat,'Укажите код темы или курса после команды.');return
            if command=='/topic':
                self.s.add('activity_events',**base,event_type='topic_selected',topic_id=values[0])
            else:
                course=self.s.db.execute('SELECT * FROM courses WHERE course_id=?',(values[0],)).fetchone()
                if not course:
                    self.send(chat,'Не найден курс. Откройте «Курсы и темы» в меню /start.');return
                if command=='/trial':
                    self.send(chat,'Пробное занятие пока не подключено.');return
                kind={'/program':'course_program_requested','/price':'price_requested','/waitlist':'waitlist_joined'}[command]
                self.s.add('activity_events',**base,event_type=kind,course_id=values[0],topic_id=course['topic_id'])
                if command=='/price':
                    price=self.s.db.execute('SELECT regular_price_minor FROM course_versions WHERE course_id=? AND effective_at<=? AND recorded_at<=? ORDER BY effective_at DESC,recorded_at DESC LIMIT 1',(values[0],utc(at),self.received)).fetchone()
                    self.send(chat,(f"{course['title']}: {price[0]/100:,.0f} ₽" if price else 'Цена пока не опубликована.'));return
                if command=='/program':
                    self.send(chat,f"Запрос программы курса «{course['title']}» записан. /manager — получить программу у менеджера.");return
        self.send(chat,'Записано.')
