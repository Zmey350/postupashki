"""Reproducible synthetic demonstration; refuses to overwrite any existing database."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import random
from config import Config
from collector import Collector
from store import Store
from postupashki_data import utc,build_features,build_marketing

START='2026-05-01';END='2026-09-12'

def generate(path='data/demo.sqlite3'):
    if Path(path).exists():raise ValueError('Демо-файл уже существует. Укажите новое имя; существующие данные не перезаписываются.')
    rng=random.Random(239);base=utc('2026-04-01');payload={}
    def put(table,**row):
        if table not in ('topics','order_items','promotion_courses') and 'recorded_at' not in row:
            row['recorded_at']=row.get('occurred_at') or row.get('created_at') or row.get('ordered_at') or row.get('observed_at') or row.get('first_seen_at') or base
        payload.setdefault(table,[]).append(row)
    def date(day,h=12):return utc(datetime(2026,5,1,tzinfo=timezone.utc)+timedelta(days=day,hours=h))
    put('topics',topic_id='ml',title='Машинное обучение')
    for cid,title,kind in [('main','Поступашки · основной','own_main'),('career','Карьера и стажировки','external'),('analytics','Аналитика сегодня','external'),('students','Студенческое сообщество','external')]:
        put('channels',channel_id=cid,title=title,kind=kind,topic_id='ml',created_at=base)
    put('telegram_channels',channel_id='main',chat_id=-100900001,recorded_at=base)
    for cid,title,price in [('ml-start','ML · Старт',2500000),('analytics-start','Аналитика · Старт',1800000)]:
        put('courses',course_id=cid,topic_id='ml',title=title,created_at=base)
        put('course_versions',version_id=cid+':v1',course_id=cid,effective_at=base,recorded_at=base,sales_open_at=base,
            sales_close_at=utc('2026-12-01'),starts_at=utc('2026-10-01'),regular_price_minor=price,variable_cost_minor=600000)
    for i,day in enumerate((30,70,112),1):
        put('promotions',promotion_id=f'promo{i}',title=['Июньский старт','Летняя скидка','Осенний набор'][i-1],kind='discount',created_at=date(day-15))
        put('promotion_versions',version_id=f'promo{i}:v1',promotion_id=f'promo{i}',effective_at=date(day-15),recorded_at=date(day-15),
            starts_at=date(day),ends_at=date(day+10),discount_type='percent',discount_value=20,promo_code=f'START{i}',terms='ДЕМО: скидка 20% на ML · Старт')
        put('promotion_courses',link_id=f'pc{i}',version_id=f'promo{i}:v1',course_id='ml-start')
    for hid,day in [('hack1',25),('hack2',80)]:
        put('hackathons',hackathon_id=hid,topic_id='ml',title='Хакатон «Первый ML-проект»' if hid=='hack1' else 'Открытая неделя аналитики',
            starts_at=date(day),ends_at=date(day+3),recorded_at=date(day-15))
        put('cost_components',cost_id=hid+':prize',hackathon_id=hid,component='prize',amount_minor=3000000,occurred_at=date(day+3))
        put('cost_components',cost_id=hid+':staff',hackathon_id=hid,component='staff',amount_minor=1800000,occurred_at=date(day+3))
    placements=[]
    for i,day in enumerate(range(0,131,10),1):
        cid=['career','analytics','students'][(i-1)%3];pid=f'p{i:02}';kind='warmup' if i%3==1 else 'ad'
        pro={30:'promo1',70:'promo2',110:'promo3'}.get(day)
        hack={20:'hack1',80:'hack2'}.get(day)
        if pro:kind='promotion'
        if hack:kind='hackathon'
        cost=(35000+5000*(i%4))*100 if i!=13 else None
        put('placements',placement_id=pid,channel_id=cid,campaign_id=pro or hack or 'guides',creative_id='creative:'+pid,published_at=date(day),recorded_at=date(day-3),quoted_cost_minor=cost,advertised_course_id='ml-start')
        put('placement_details',placement_id=pid,activity_type=kind,ends_at=date(day+3),hackathon_id=hack,recorded_at=date(day-3))
        if cost:put('placement_cost_events',cost_event_id=pid+':cost',placement_id=pid,kind='expense',amount_minor=cost,occurred_at=date(day))
        put('tracking_links',link_id='start:'+pid,source_token=pid,source_channel_id=cid,placement_id=pid,mechanism='bot_start',created_at=date(day-1))
        put('tracking_links',link_id='invite:'+pid,source_token='https://t.me/+DEMO_'+pid,source_channel_id=cid,placement_id=pid,destination_channel_id='main',mechanism='invite_link',created_at=date(day-1))
        if pro:put('placement_promotions',placement_id=pid,promotion_id=pro,recorded_at=max(date(day-3),date({'promo1':15,'promo2':55,'promo3':97}[pro])))
        placements.append((pid,cid,day,pro,hack))
    count=0
    for pid,cid,day,pro,hack in placements:
        for j in range(20+day//15):
            count+=1;uid=200000+count;at=date(day+rng.randrange(0,3),13);mode=rng.choices(['clean','mixed','unknown'],[.77,.14,.09])[0]
            put('users',user_id=uid,first_seen_at=at)
            if mode=='clean':
                put('acquisition_events',event_id=f'u{uid}:entry',user_id=uid,source_channel_id=cid,placement_id=pid,mechanism='bot_start',source_token=pid,occurred_at=at)
            elif mode=='mixed':
                put('manual_sources',event_id=f'u{uid}:entry',user_id=uid,source_channel_id=cid,placement_id=pid,answer=cid,occurred_at=at)
            else:
                put('acquisition_events',event_id=f'u{uid}:entry',user_id=uid,mechanism='unknown',occurred_at=at)
            put('activity_events',event_id=f'u{uid}:start',user_id=uid,event_type='bot_started',occurred_at=at)
            put('profile_events',event_id=f'u{uid}:profile',user_id=uid,field='education_stage',value='university',source='bot_form',occurred_at=at)
            if rng.random()<.84:put('membership_events',event_id=f'u{uid}:join',user_id=uid,channel_id='main',status='joined',occurred_at=at)
            if rng.random()<.2 and day<105:put('membership_events',event_id=f'u{uid}:leave',user_id=uid,channel_id='main',status='left',occurred_at=date(day+20))
            if hack and j<18:
                hday=25 if hack=='hack1' else 80
                # Registration cannot be earlier than the user's first observation.
                reg=max(at,date(hday-1,14));submit=date(hday+2,14)
                put('participation_events',event_id=f'u{uid}:reg',user_id=uid,hackathon_id=hack,status='registered',occurred_at=reg)
                if j<12:
                    put('participation_events',event_id=f'u{uid}:submit',user_id=uid,hackathon_id=hack,status='solution_submitted',occurred_at=submit)
                    put('submission_links',event_id=f'u{uid}:submit',url=f'https://example.org/demo/{uid}',recorded_at=submit)
                if j<9:put('completion_events',event_id=f'u{uid}:complete',user_id=uid,hackathon_id=hack,confirmed_by=999001,occurred_at=date(hday+3,13))
            if rng.random()<.70:
                lead_at=date(day+3,14)
                put('leads',lead_id=f'l{uid}',user_id=uid,interest='ML · Старт',created_at=lead_at)
                put('activity_events',event_id=f'u{uid}:price',user_id=uid,event_type='price_requested',course_id='ml-start',occurred_at=lead_at)
                if rng.random()<.52:
                    purchase_day=day+rng.choice([4,5,7,10,25,40,60])
                    if purchase_day>=134:continue
                    o_at=date(purchase_day,15);total=2500000;match=None
                    for proid,pday in [('promo1',30),('promo2',70),('promo3',112)]:
                        if pday<=purchase_day<pday+10:match=proid;total=2000000
                    oid=f'o{uid}';put('orders',order_id=oid,user_id=uid,ordered_at=o_at,total_minor=total)
                    put('order_items',item_id=oid+':1',order_id=oid,course_id='ml-start',line_total_minor=total,variable_cost_minor=600000)
                    put('order_leads',order_id=oid,lead_id=f'l{uid}',recorded_at=o_at)
                    put('payments',payment_id=oid+':pay',order_id=oid,kind='payment',amount_minor=total,fee_minor=total//50,occurred_at=o_at)
                    if match:put('order_promotions',order_id=oid,version_id=match+':v1',evidence='promo_code',recorded_at=o_at)
                    if rng.random()<.07 and purchase_day<127:put('payments',payment_id=oid+':refund',order_id=oid,kind='refund',amount_minor=total//2,fee_minor=0,occurred_at=date(purchase_day+7))
    for day in range(134):
        for domain in ('acquisition','membership','activity','commerce','participation','offers','ad_costs','promotion_offers','promotion_responses','order_promotions'):
            put('collection_periods',period_id=f'{domain}:{day}',domain=domain,starts_at=date(day,0),ends_at=date(day+1,0),recorded_at=date(day+1,0))
        if day%7==0:
            for cid,n in [('career',42000),('analytics',27000),('students',18000),('main',1200+day*3)]:
                put('channel_snapshots',snapshot_id=f'{cid}:{day}',channel_id=cid,observed_at=date(day),subscribers=n+day//4,typical_post_views_24h=n//4,source='manual')
    with Store(path) as s:
        with s.db:s.db.execute("UPDATE app_meta SET value='synthetic' WHERE key='provenance'")
        s.load(payload);s.bind('telegram_live')
        # Exercise the same Collector used in production with an actual-shaped update.
        cfg=Config(db=Path(path),channels=frozenset({-100900001}),manager='demo_manager',admins=frozenset({999001}))
        bot=Collector(s,cfg,777001)
        update=dict(update_id=1,message=dict(message_id=1,date=int(datetime(2026,9,10,12,tzinfo=timezone.utc).timestamp()),
                    chat=dict(id=900001,type='private'),**{'from':dict(id=900001,is_bot=False)},text='/start p14'))
        assert bot.ingest(update,received_at=utc('2026-09-10T12:00:01Z'))=='processed'
        assert bot.ingest(update)=='processed'
        build_features(s.db,utc('2026-09-12'));build_marketing(s.db,utc('2026-09-12'))
        from forecast import build_baselines
        build_baselines(s,utc('2026-09-01T12:00:00Z'),14)
        s.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return f'Синтетическая демонстрация: {path}; {count} пользователей + проверка Collector'
