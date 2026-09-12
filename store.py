"""The latest data package is the sole writer of business facts."""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re
import sqlite3
from postupashki_data import connect, initialize, utc
from postupashki_data.db import record, validate, ingest
from postupashki_data.catalog import RAW_TABLES

DataError = ValueError
EXT_TABLES = ['telegram_channels','placement_details','acquisition_notes','manual_sources','tracked_clicks',
              'leads','order_leads','submission_links','completion_events','join_requests','cost_components',
              'incrementality_evidence','forecast_runs','forecast_points']
ALL_TABLES = [*RAW_TABLES, *EXT_TABLES]


def ident(x):
    if not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',x):
        raise DataError('Код должен содержать 1–128 латинских букв, цифр или символов _ . : -')
    return x


class Store:
    def __init__(self,path):
        self.path = Path(path) if str(path)!=':memory:' else None
        self.db = connect(path)
        # Do not initialize the incompatible MVP v1/v2 in place.
        cols = {r[1] for r in self.db.execute('PRAGMA table_info(users)')}
        if cols and 'user_id' not in cols:
            self.db.close()
            raise DataError('Это старая база MVP. Используйте новую postupashki.sqlite3; старую базу сохраните отдельно.')
        self.db.execute('PRAGMA journal_mode=WAL')
        initialize(self.db)
        self.db.executescript(Path(__file__).with_name('extension.sql').read_text(encoding='utf-8'))
        self.db.commit()
        if self.db.execute("SELECT value FROM app_meta WHERE key='schema'").fetchone()[0]!='1':
            raise DataError('Версия приложения новее этой сборки')

    def __enter__(self): return self
    def __exit__(self,*args): self.db.close()

    def bind(self,dataset):
        old=self.db.execute("SELECT value FROM app_meta WHERE key='dataset'").fetchone()
        if old and old[0]!=dataset:
            raise DataError('У этой базы уже другой DATASET_ID. Демо и реальные данные должны быть в разных файлах.')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO app_meta VALUES ('dataset',?)",(dataset,))

    def add(self,table,**row): return record(self.db,table,row,allowed_tables=ALL_TABLES)

    def user(self,uid,at,received):
        if type(uid)!=int or uid<=0: raise DataError('Нужен проверенный Telegram user ID')
        old=self.db.execute('SELECT first_seen_at FROM users WHERE user_id=?',(uid,)).fetchone()
        if not old:
            self.add('users',user_id=uid,first_seen_at=at,recorded_at=received)
        # Late updates from another bot can precede the first event seen so far.
        # Move only the derived first observation backwards; original facts stay immutable.
        elif utc(at)<old[0]:
            self.db.execute('UPDATE users SET first_seen_at=? WHERE user_id=?',(utc(at),uid))

    def channel(self,chat):
        row=self.db.execute('SELECT c.* FROM telegram_channels t JOIN channels c USING(channel_id) WHERE t.chat_id=?',(chat,)).fetchone()
        if not row: raise DataError(f'Канал {chat} не связан с channels: выполните bind-channel')
        return dict(row)

    def resolve_token(self,ds,token):
        row=self.db.execute('''SELECT l.*,p.published_at FROM tracking_links l LEFT JOIN placements p USING(placement_id)
                               WHERE l.source_token=? AND l.mechanism='bot_start' ''',(token,)).fetchone()
        if not row:return None
        r=dict(row);r['id']=r['placement_id'];r['publication_us']=self.stamp(ds,r['published_at'] or r['created_at'])[1]
        return r

    def resolve_invite(self,ds,chat,link):
        row=self.db.execute('''SELECT l.* FROM tracking_links l JOIN telegram_channels t
                   ON t.channel_id=l.destination_channel_id WHERE t.chat_id=? AND l.source_token=? AND l.mechanism='invite_link' ''',(chat,link)).fetchone()
        return dict(row) if row else None

    def stamp(self,ds,value):
        at=utc(value)
        return at,int(datetime.fromisoformat(at.replace('Z','+00:00')).timestamp()*1e6)

    def validate(self):
        validate(self.db)
        checks=[
          ('''SELECT 1 FROM order_leads x JOIN orders o USING(order_id) JOIN leads l USING(lead_id)
                 WHERE o.user_id!=l.user_id OR l.created_at>o.ordered_at''','Заявка не соответствует пользователю/времени заказа'),
          ('''SELECT 1 FROM completion_events e WHERE NOT EXISTS (SELECT 1 FROM participation_events p
                 WHERE p.user_id=e.user_id AND p.hackathon_id=e.hackathon_id AND p.status='solution_submitted' AND p.occurred_at<=e.occurred_at)''','Завершение без ранее сданной работы'),
          ('''SELECT 1 FROM telegram_channels t JOIN channels c USING(channel_id) WHERE c.kind='external' ''','Наблюдать можно только свой канал'),
          ('''SELECT 1 FROM forecast_points p JOIN forecast_runs r USING(run_id) WHERE p.day<=substr(r.origin_at,1,10)''','Прогноз должен относиться к будущим дням'),
          ('''SELECT 1 FROM incrementality_evidence WHERE method='calibration' AND (k<0 OR k>1)''','Коэффициент дополнительной выручки k должен быть от 0 до 1'),
        ]
        for sql,error in checks:
            if self.db.execute(sql+' LIMIT 1').fetchone():raise DataError(error)
        for table in ('manual_sources','tracked_clicks'):
            if self.db.execute(f'''SELECT 1 FROM {table} t JOIN placements p USING(placement_id)
                WHERE t.source_channel_id IS NOT p.channel_id OR t.occurred_at<p.published_at LIMIT 1''').fetchone():
                raise DataError('Источник/дата касания не соответствуют размещению')
        for r in self.db.execute('SELECT * FROM forecast_runs'):
            campaigns=json.loads(r['known_campaigns_json'])
            if not isinstance(campaigns,list) or any(utc(c['known_at'])>r['origin_at'] for c in campaigns):
                raise DataError('Прогноз содержит кампании, неизвестные на дату прогноза')
            if r['interval_level'] is not None and not 0<r['interval_level']<1:
                raise DataError('Уровень интервала должен быть между 0 и 1')
            for c in campaigns:
                if c.get('kind')=='placement':
                    known=self.db.execute('SELECT recorded_at FROM placements WHERE placement_id=?',(c.get('id'),)).fetchone()
                elif c.get('kind')=='promotion':
                    known=self.db.execute('SELECT recorded_at FROM promotion_versions WHERE version_id=? AND promotion_id=?',(c.get('version_id'),c.get('id'))).fetchone()
                else:raise DataError('Неизвестный тип кампании в прогнозе')
                if not known or known[0]!=utc(c['known_at']) or known[0]>r['origin_at']:
                    raise DataError('known_at кампании не совпадает с реестром')

    def load(self,payload):
        if not isinstance(payload,dict) or set(payload)-set(ALL_TABLES):
            raise DataError('Нужен JSON-контракт последней БД: {имя_таблицы: [строки]}')
        result={'inserted':0,'duplicates':0}
        # SAVEPOINT keeps base ingest and extra checks in one atomic transaction.
        # Reuse base ingest rules while retaining the outer transaction.
        from postupashki_data.db import ingest_rows
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            result=ingest_rows(self.db,payload,tables=ALL_TABLES)
            self.validate()
        return result

    def import_file(self,path):
        path=Path(path);content=path.read_bytes();digest=hashlib.sha256(content).hexdigest()
        ds=self.db.execute("SELECT value FROM app_meta WHERE key='dataset'").fetchone()
        ds=ds[0] if ds else 'telegram_live'
        try:
            result=self.load(json.loads(content.decode('utf-8-sig')))
        except (ValueError,TypeError,sqlite3.Error) as e:
            with self.db:self.db.execute('INSERT OR REPLACE INTO import_runs VALUES(?,?,?,?,?,?)',(ds,digest,path.name,utc(),'failed',str(e)[:1000]))
            raise DataError(f'{path.name}: {e}') from None
        with self.db:self.db.execute('INSERT OR REPLACE INTO import_runs VALUES(?,?,?,?,?,NULL)',(ds,digest,path.name,utc(),'succeeded'))
        return result
