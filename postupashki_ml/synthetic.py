"""Explicit simulator for integration tests; its hidden law is NOT a learned result."""
import json
from pathlib import Path
import numpy as np
from scipy.special import expit
from postupashki_data.db import connect,initialize,validate
from .contracts import stamp,after,Plan


def generate(path,seed=42,users_per_wave=120,rounds=12):
    path=Path(path)
    if path.exists():
        raise FileExistsError('Генератор создаёт только новую отдельную БД; существующую не перезаписывает')
    if users_per_wave<20 or rounds<8:
        raise ValueError('Для демонстрации подбора нужно users_per_wave>=20, rounds>=8')
    rng=np.random.default_rng(seed)
    # Build independently in RAM, publish a complete database without journal sidecars.
    # This also prevents partially generated demo data from resembling a valid dataset.
    path.parent.mkdir(parents=True,exist_ok=True)
    con=connect(':memory:');initialize(con)
    con.executescript(Path(__file__).with_name('schema.sql').read_text())
    con.execute("UPDATE ml_meta SET value='synthetic' WHERE key='provenance'")
    con.execute("INSERT INTO ml_meta VALUES ('seed',?)",(str(seed),))
    origin=stamp('2025-02-01');cutoff=stamp('2026-09-01');uid=0;serial=0
    def put(table,**r):
        con.execute('INSERT INTO '+table+' ('+','.join(r)+') VALUES ('+','.join('?' for _ in r)+')',list(r.values()))
    def ident(prefix):
        nonlocal serial
        serial+=1;return f'{prefix}{serial}'
    put('topics',topic_id='ml',title='ML и аналитика')
    put('channels',channel_id='main',title='Наш учебный канал',kind='own_main',topic_id='ml',created_at=origin,recorded_at=origin)
    for i in range(8):
        put('channels',channel_id=f'ch{i}',title=f'Синтетический канал {i}',kind='external',topic_id='ml',created_at=origin,recorded_at=origin)
    put('courses',course_id='ml_course',topic_id='ml',title='Учебный синтетический курс',created_at=origin,recorded_at=origin)
    put('course_versions',version_id='course-v1',course_id='ml_course',effective_at=origin,recorded_at=origin,sales_open_at=origin,sales_close_at=stamp('2026-12-31'),starts_at=stamp('2027-01-10'),regular_price_minor=2000000,variable_cost_minor=450000)
    plans=[Plan(),Plan(contacts=[{'day':0,'kind':'message'},{'day':5,'kind':'message'}]),
           Plan(discount_pct=20,discount_days=14),
           Plan(discount_pct=20,discount_days=14,contacts=[{'day':0,'kind':'message'},{'day':4,'kind':'event'},{'day':9,'kind':'message'}])]
    for i,p in enumerate(plans):
        put('promotions',promotion_id=f'promo{i}',title=f'Синтетический вариант {i}',kind='discount' if p.discount_pct else 'warmup',created_at=origin,recorded_at=origin)
        put('promotion_versions',version_id=f'promo-v{i}',promotion_id=f'promo{i}',effective_at=origin,recorded_at=origin,starts_at=origin,ends_at=stamp('2026-12-31'),discount_type='percent' if p.discount_pct else 'none',discount_value=int(p.discount_pct),terms='Тестовый случайный эксперимент')
        put('promotion_courses',link_id=f'promo-course{i}',version_id=f'promo-v{i}',course_id='ml_course')
    # Cumulative confirmations are appended only after collection actually ends.
    for d in range(1,578):
        end=after(origin,d)
        if end>cutoff:break
        for domain in ('acquisition','membership','activity','commerce','participation','offers','ad_costs','promotion_offers','promotion_responses','order_promotions'):
            put('collection_periods',period_id=f'{domain}-{d}',domain=domain,starts_at=origin,ends_at=end,recorded_at=end)
        put('ml_contact_periods',period_id=f'contacts-{d}',starts_at=origin,ends_at=end,recorded_at=end)
    def user(first,source=None,placement=None):
        nonlocal uid
        uid+=1;u=uid
        put('users',user_id=u,first_seen_at=first,recorded_at=first)
        if source:
            put('acquisition_events',event_id=ident('aq'),user_id=u,source_channel_id=source,placement_id=placement,mechanism='bot_start',source_token=placement,occurred_at=first,recorded_at=first)
        put('membership_events',event_id=ident('member'),user_id=u,channel_id='main',status='joined',occurred_at=first,recorded_at=first)
        stage=str(rng.choice(['university','graduate','school'],p=[.65,.25,.1]))
        job=str(rng.choice(['internship','exploring','not_searching'],p=[.4,.4,.2]))
        for field,value in [('education_stage',stage),('job_search_status',job)]:
            put('profile_events',event_id=ident('profile'),user_id=u,field=field,value=value,source='bot_form',occurred_at=first,recorded_at=first)
        return u,job
    def purchase(u,t,arm,order_promo=False):
        price=int(2000000*(1-plans[arm].discount_pct/100));oid=ident('order')
        put('orders',order_id=oid,user_id=u,ordered_at=t,recorded_at=t,total_minor=price)
        put('order_items',item_id=ident('item'),order_id=oid,course_id='ml_course',quantity=1,line_total_minor=price,variable_cost_minor=450000)
        # Some orders settle in two installments, then occasionally refund.
        part=price//3
        if rng.random()<.2:
            put('payments',payment_id=ident('pay'),order_id=oid,kind='payment',amount_minor=part,fee_minor=int(part*.02),occurred_at=t,recorded_at=t)
            put('payments',payment_id=ident('pay'),order_id=oid,kind='payment',amount_minor=price-part,fee_minor=int((price-part)*.02),occurred_at=after(t,.2),recorded_at=after(t,.2))
        else:
            put('payments',payment_id=ident('pay'),order_id=oid,kind='payment',amount_minor=price,fee_minor=int(price*.02),occurred_at=t,recorded_at=t)
        if rng.random()<.04:
            put('payments',payment_id=ident('refund'),order_id=oid,kind='refund',amount_minor=price,fee_minor=0,occurred_at=after(t,8),recorded_at=after(t,8))
        if order_promo:
            put('order_promotions',order_id=oid,version_id=f'promo-v{arm}',evidence='manual_confirmed',recorded_at=t)
    # Channel quality, declining supply of unseen responders, overlapping observations.
    for r in range(rounds):
        for c in range(8):
            t=after(origin,20+r*(470/max(1,rounds-1))+c*3)
            pid=f'placement-{r}-{c}';arm=int(rng.integers(0,4));plan=plans[arm]
            decay=.89**r if c in (0,1,2) else .985**r
            views=int((4500+c*800)*decay)
            put('channel_snapshots',snapshot_id=ident('snapshot'),channel_id=f'ch{c}',observed_at=after(t,-1),recorded_at=after(t,-1),subscribers=12000+c*2000,typical_post_views_24h=views,audience_stage='university',source='manual')
            put('placements',placement_id=pid,channel_id=f'ch{c}',campaign_id=f'round{r}',creative_id='creative'+str(r%3),published_at=t,recorded_at=after(t,-2),quoted_cost_minor=1500000,advertised_course_id='ml_course')
            put('placement_cost_events',cost_event_id=ident('expense'),placement_id=pid,kind='expense',amount_minor=1500000,occurred_at=t,recorded_at=t)
            put('ml_placement_plans',placement_id=pid,plan_json=plan.key(),recorded_at=after(t,-1))
            put('placement_promotions',placement_id=pid,promotion_id=f'promo{arm}',recorded_at=after(t,-1))
            n=int(rng.poisson(views*(.007+.0005*c)))
            for j in range(n):
                arrival=after(t,min(float(rng.exponential(1.7)),6.8))
                u,job=user(arrival,f'ch{c}',pid)
                p=expit(-2.9+.13*c+.65*(job=='internship')+.028*plan.discount_pct+.12*len(plan.contacts))
                if rng.random()<p:purchase(u,after(arrival,float(rng.uniform(.3,12))),arm,True)
            # Cross-source and same-source returnees, always already known by event time.
            existing=[x[0] for x in con.execute('SELECT user_id FROM users WHERE first_seen_at<? ORDER BY user_id DESC LIMIT 500',(t,))]
            for u in rng.choice(existing,size=min(len(existing),10+r*2),replace=False) if existing else []:
                at=after(t,float(rng.uniform(.1,6)))
                put('acquisition_events',event_id=ident('return'),user_id=int(u),source_channel_id=f'ch{c}',placement_id=pid,mechanism='bot_start',source_token=pid,occurred_at=at,recorded_at=at)
    # A different simulator from the candidate kernels: a mixture of short and long memory.
    for wave in range(18):
        t=after(origin,50+wave*27)
        for j in range(users_per_wave):
            u,job=user(after(t,-float(rng.uniform(12,35))))
            activity=int(rng.poisson(2.5));warm=0.
            for k in range(activity):
                ago=float(rng.uniform(.5,10));at=after(t,-ago)
                put('activity_events',event_id=ident('act'),user_id=u,event_type='price_requested' if k%2 else 'topic_selected',topic_id='ml',course_id='ml_course',occurred_at=at,recorded_at=at)
                put('ml_contacts',contact_id=ident('msg'),user_id=u,course_id='ml_course',kind='message',evidence='delivered',intensity=1.,occurred_at=at,recorded_at=at)
                warm+=.6*np.exp(-ago/3)+.4*np.exp(-ago/12)
            arm=int(rng.integers(0,4));plan=plans[arm]
            put('ml_decisions',decision_id=f'decision-{u}',user_id=u,course_id='ml_course',as_of=t,recorded_at=t,horizon_days=14,plan_json=plan.key(),assignment_kind='randomized',experiment_id=f'wave-{wave}',arm=str(arm),assignment_probability=.25)
            # Assignment is the feature. Future actual delivery is a different event.
            for contact in plan.contacts:
                at=after(t,contact['day']+.01)
                put('ml_contacts',contact_id=ident('delivery'),user_id=u,course_id='ml_course',kind=contact['kind'],evidence='delivered',intensity=1.,occurred_at=at,recorded_at=at)
            if plan.discount_pct:
                offer=ident('offer');at=after(t,.01)
                put('offers',offer_id=offer,user_id=u,course_id='ml_course',regular_price_minor=2000000,offered_price_minor=1600000,status='sent',sent_at=at,recorded_at=at,valid_until=after(t,14),experiment_id=f'wave-{wave}',experiment_group=str(arm))
                put('promotion_offers',offer_id=offer,version_id=f'promo-v{arm}',assignment_reason='randomized',recorded_at=at)
            pressure=activity+len(plan.contacts)
            p=expit(-3.1+.75*(job=='internship')+.17*activity+.65*warm/(1+warm)+.032*plan.discount_pct+.18*len(plan.contacts)-.045*max(0,pressure-5)**2)
            if rng.random()<p:purchase(u,after(t,float(rng.uniform(.2,12.5))),arm,True)
    con.commit()
    issues=validate(con)
    if issues:raise ValueError('Synthetic base validation failed: '+str(issues)[:2000])
    counts={t:con.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('users','placements','orders','payments','ml_decisions','ml_contacts')}
    if hasattr(con,'serialize'):
        with path.open('xb') as target:target.write(con.serialize())
    else:
        with connect(path) as target:con.backup(target)
        target.close()
    con.close()
    return {'db':str(path),'cutoff':cutoff,'provenance':'synthetic','seed':seed,'counts':counts,
            'note':'Это искусственный мир с заданными зависимостями, не оценка реального эффекта акций.'}
