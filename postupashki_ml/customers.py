"""Point-in-time customer features and frozen-plan, fixed-horizon S-learner data."""
import json
import numpy as np
import pandas as pd
from postupashki_data.features import _compute, _covered
from .contracts import stamp, after, days, Plan, catalog, paid_orders, covered, table_exists

CATEGORICAL=['first_source_channel_id','education_stage','job_search_status','primary_topic_id','course_topic']
KINDS=('offer','message','event','ad')


class CustomerContext:
    """One history read per date/course; no SELECT per customer or scenario."""
    def __init__(self, con, course_id, as_of, horizon=14):
        self.course_id=course_id
        self.as_of=stamp(as_of)
        self.horizon=horizon
        self.periods={}
        for r in con.execute('SELECT domain,starts_at,ends_at FROM collection_periods WHERE recorded_at<=? ORDER BY starts_at', (self.as_of,)):
            self.periods.setdefault(r[0],[]).append((r[1],r[2]))
        if table_exists(con,'ml_contact_periods'):
            self.periods['contacts']=[tuple(r) for r in con.execute('SELECT starts_at,ends_at FROM ml_contact_periods WHERE recorded_at<=? ORDER BY starts_at',(self.as_of,))]
        for domain,intervals in self.periods.items():
            merged=[]
            for left,right in intervals:
                if merged and left<=merged[-1][1]:merged[-1]=(merged[-1][0],max(right,merged[-1][1]))
                else:merged.append((left,right))
            self.periods[domain]=merged
        self.course=catalog(con,course_id,self.as_of)
        self.base=pd.DataFrame(_compute(con,self.as_of))
        if self.base.empty:
            raise ValueError('Нет пользователей, известных на дату прогноза')
        self.base=self.base.set_index('user_id').drop(columns=['as_of','feature_version','quality_json','built_at'])
        self.base['course_topic']=self.course['topic_id']
        self.base['course_regular_price']=self.course['regular_price_minor']/100
        self.base['course_days_to_start']=days(self.course['starts_at'],self.as_of)
        self.base['course_days_to_sales_close']=days(self.course['sales_close_at'],self.as_of)
        self.base['topic_match']=(self.base.primary_topic_id==self.course['topic_id']).astype(float)
        self.base['weekday']=pd.Timestamp(self.as_of).dayofweek
        self.base['month_sin']=np.sin(2*np.pi*self.base.calendar_month/12)
        self.base['month_cos']=np.cos(2*np.pi*self.base.calendar_month/12)
        self.offers=pd.read_sql_query('''SELECT * FROM offers WHERE course_id=? AND status='sent'
            AND sent_at<? AND recorded_at<=? ORDER BY sent_at,recorded_at,offer_id''',con,params=(course_id,self.as_of,self.as_of))
        chunks=[]
        if not self.offers.empty:
            o=self.offers.rename(columns={'sent_at':'occurred_at'}).copy()
            o['kind']='offer'; o['intensity']=1.
            chunks.append(o[['user_id','occurred_at','kind','intensity']])
        if table_exists(con,'ml_contacts'):
            c=pd.read_sql_query('''SELECT user_id,occurred_at,kind,intensity FROM ml_contacts
                WHERE (course_id=? OR course_id IS NULL) AND occurred_at<? AND recorded_at<=?''',con,params=(course_id,self.as_of,self.as_of))
            chunks.append(c)
        a=pd.read_sql_query('''SELECT a.user_id,a.occurred_at FROM acquisition_events a
             JOIN placements p USING(placement_id) WHERE p.advertised_course_id=?
             AND a.occurred_at<? AND a.recorded_at<=? AND p.recorded_at<=? AND p.published_at<?''',con,params=(course_id,self.as_of,self.as_of,self.as_of,self.as_of))
        a['kind']='ad';a['intensity']=1.
        chunks.append(a)
        chunks=[z for z in chunks if not z.empty]
        self.contacts=pd.concat(chunks,ignore_index=True) if chunks else pd.DataFrame(columns=['user_id','occurred_at','kind','intensity'])
        if not self.contacts.empty:
            self.contacts['age']=(pd.Timestamp(self.as_of)-pd.to_datetime(self.contacts.occurred_at,utc=True)).dt.total_seconds()/86400
        else:
            self.contacts['age']=pd.Series(dtype=float)
        paid=paid_orders(con,self.as_of)
        items=pd.read_sql_query('SELECT DISTINCT order_id,course_id FROM order_items WHERE course_id=?',con,params=(course_id,))
        owned=paid[paid.balance_minor>0].merge(items,on='order_id').user_id if not paid.empty else []
        self.eligible=self.base.index[~self.base.index.isin(owned)]

    def features(self, decay, plan=None, ids=None):
        plan=Plan.parse(plan,self.horizon)
        idx=self.base.index if ids is None else pd.Index(ids,name='user_id')
        if not idx.is_unique:
            raise ValueError('Повторяющиеся user_id в прогнозе')
        x=self.base.loc[idx].copy()
        n=len(x);h=self.horizon
        prices=np.full((n,h),float(self.course['regular_price_minor']))
        if not self.offers.empty:
            latest=self.offers.drop_duplicates('user_id',keep='last').set_index('user_id').reindex(idx)
            rem=(pd.to_datetime(latest.valid_until,utc=True)-pd.Timestamp(self.as_of)).dt.total_seconds().to_numpy()/86400
            op=pd.to_numeric(latest.offered_price_minor).to_numpy()
            for d in range(h):
                active=(rem>d)&np.isfinite(op)
                prices[active,d]=np.minimum(prices[active,d],op[active])
        if plan.discount_pct:
            prices[:,:min(h,plan.discount_days)]=np.minimum(prices[:,:min(h,plan.discount_days)],self.course['regular_price_minor']*(1-plan.discount_pct/100))
        regular=max(self.course['regular_price_minor'],1)
        x['offered_price_now']=prices[:,0]/100
        x['offered_price_mean']=prices.mean(axis=1)/100
        x['discount_mean']=1-prices.mean(axis=1)/regular
        x['discount_days_fraction']=(prices<regular).mean(axis=1)
        x['discount_at_end']=1-prices[:,-1]/regular
        for kind in KINDS:
            cs=self.contacts[self.contacts.kind==kind]
            cs=cs[cs.user_id.isin(idx)&(cs.age<=decay.max_lag)]
            values=np.zeros((n,h))
            ix=idx.get_indexer(cs.user_id)
            age=cs.age.to_numpy(dtype=float)
            intensity=cs.intensity.to_numpy(dtype=float)
            for d in range(h):
                if len(cs):
                    np.add.at(values[:,d],ix,intensity*decay.weights(age+d))
            x[kind+'_warm_now']=decay.saturate(values[:,0])
            for c in plan.contacts:
                if c['kind']==kind:
                    values+=c.get('intensity',1)*decay.weights(np.arange(h)-c['day'])[None,:]
            x[kind+'_warm_mean']=decay.saturate(values).mean(axis=1)
            x[kind+'_warm_end']=decay.saturate(values[:,-1])
            counts=cs[cs.age<7].groupby('user_id').intensity.sum().reindex(idx,fill_value=0).to_numpy()
            x[kind+'_contacts_7d']=counts
            planned=sum(c.get('intensity',1) for c in plan.contacts if c['kind']==kind)
            x[kind+'_planned_contacts']=planned
            # Separate pressure permits fatigue/negative response; no forced positive effect.
            x[kind+'_pressure']=counts+planned
            domain='offers' if kind=='offer' else 'acquisition' if kind=='ad' else 'contacts'
            known=np.array([_covered(self.periods,domain,after(self.as_of,-min(decay.max_lag,age)),self.as_of) for age in x.days_since_first_seen])
            x[kind+'_history_complete']=known.astype(float)
            for suffix in ('warm_now','warm_mean','warm_end','contacts_7d','pressure'):
                x.loc[~known,kind+'_'+suffix]=np.nan
        for c in CATEGORICAL:
            x[c]=x[c].fillna('unknown').astype(str)
        for c in x.columns.difference(CATEGORICAL):
            x[c]=pd.to_numeric(x[c],errors='coerce').astype(float)
        pricing=pd.DataFrame({'price_mean_minor':prices.mean(axis=1),'price_min_minor':prices.min(axis=1),'price_max_minor':prices.max(axis=1)},index=idx)
        return x,pricing


def decisions(con, cutoff, horizon):
    if table_exists(con,'ml_decisions'):
        d=pd.read_sql_query('SELECT * FROM ml_decisions WHERE recorded_at<=? AND horizon_days=? ORDER BY as_of,user_id',con,params=(cutoff,horizon))
        if not d.empty:
            # Full mature outcomes only; malformed overlapping assignments fail early.
            d['label_end']=d.as_of.map(lambda t:after(t,horizon))
            d=d[d.label_end<=cutoff].copy()
            for _,g in d.sort_values('as_of').groupby(['user_id','course_id']):
                if (g.as_of.iloc[1:].to_numpy()<g.label_end.iloc[:-1].to_numpy()).any():
                    raise ValueError('Перекрывающиеся окна ml_decisions; исправьте журнал решений')
            return d
    # Observational fallback: planned schedule is EMPTY, never read from future delivery logs.
    users=pd.read_sql_query('SELECT * FROM users',con)
    if users.empty:
        raise ValueError('Нет данных для обучения')
    start=after(users.first_seen_at.min(),30)
    cs=[r[0] for r in con.execute('SELECT course_id FROM courses')]
    rows=[]
    for t in pd.date_range(start,after(cutoff,-horizon),freq=f'{horizon}D'):
        t=stamp(t)
        for course in cs:
            try:
                c=catalog(con,course,t)
            except ValueError:
                continue
            if c['is_cancelled'] or not c['sales_open_at']<=t<c['sales_close_at']:
                continue
            for uid in users.loc[(users.first_seen_at<t)&(users.recorded_at<=t),'user_id']:
                rows.append(dict(decision_id=f'natural:{uid}:{course}:{t}',user_id=uid,course_id=course,as_of=t,recorded_at=t,horizon_days=horizon,plan_json=Plan().key(),assignment_kind='observational',experiment_id=None,arm=None,assignment_probability=None,label_end=after(t,horizon)))
    return pd.DataFrame(rows)


def training_frames(con, cutoff, horizon, decays):
    cutoff=stamp(cutoff)
    d=decisions(con,cutoff,horizon)
    if d.empty:
        raise ValueError('Нет решений с полностью завершённым горизонтом')
    p=paid_orders(con,cutoff)
    it=pd.read_sql_query('SELECT DISTINCT order_id,course_id FROM order_items',con)
    p=p.merge(it,on='order_id')
    frames={v.key():[] for v in decays}; labels=[]; meta=[]; dropped=0
    for (t,course),g in d.groupby(['as_of','course_id'],sort=True):
        end=after(t,horizon)
        if not covered(con,'commerce',t,end,cutoff):
            dropped+=len(g);continue
        ctx=CustomerContext(con,course,t,horizon)
        g=g[g.user_id.isin(ctx.eligible)]
        for key,gg in g.groupby('plan_json',sort=True):
            ids=gg.user_id.to_list()
            plan=Plan.parse(key,horizon)
            bought=set(p.loc[(p.course_id==course)&(p.paid_at>=t)&(p.paid_at<end),'user_id'])
            labels.extend(int(uid in bought) for uid in ids)
            meta.extend(dict(time=t,end=end,user_id=r.user_id,course_id=course,plan=plan.key(),experiment=r.experiment_id,arm=r.arm,assignment=r.assignment_kind,probability=r.assignment_probability) for r in gg.itertuples())
            for decay in decays:
                x,_=ctx.features(decay,plan,ids)
                frames[decay.key()].append(x.reset_index(drop=True))
    if not labels:
        raise ValueError('Нет пригодных меток: нужны полные интервалы commerce и доступные покупателям курсы')
    return {k:pd.concat(v,ignore_index=True) for k,v in frames.items()},np.array(labels),pd.DataFrame(meta),{'dropped_incomplete':dropped,'rows':len(labels),'buyers':int(sum(labels))}


def experiment_report(meta,y):
    """Empirical ITT descriptions, no claim that a log label proves randomization."""
    d=meta.copy();d['y']=y
    reports=[]
    for e,g in d[d.assignment=='randomized'].groupby('experiment'):
        arms=[]
        for a,t in g.groupby('arm'):
            arms.append({'arm':a,'n':len(t),'buyers':int(t.y.sum()),'rate':float(t.y.mean()),'plans':sorted(t.plan.unique().tolist())})
        reports.append({'experiment':e,'arms':arms,'note':'Назначение должно быть реально случайным. Это описательный ITT; перенос эффекта на другие аудитории требует проверки.'})
    return reports
