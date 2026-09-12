"""Two untrained baselines; rolling-origin errors use only then-known data."""
from datetime import timedelta
from statistics import mean
from uuid import uuid4
import json
from analytics import Analytics,dt,add,ratio
from postupashki_data import utc


def predict(values,last_day,target,model):
    if not values:return None
    if model=='naive':return values[-1]
    if len(values)<7:return None
    i=len(values)-1+(target-last_day).days
    while i>=len(values):i-=7
    return values[i] if i>=0 else None


def series(con,origin,days=180):
    end=utc(dt(origin).replace(hour=0,minute=0,second=0,microsecond=0))
    start=add(end,-days);a=Analytics(con,start,end,knowledge=origin)
    # Use the longest fully covered daily suffix, never replace a collection gap by zero.
    values=[];last=dt(end).date()-timedelta(days=1)
    money={}
    for p in a.payments:money[p['occurred_at'][:10]]=money.get(p['occurred_at'][:10],0)+p['net_minor']
    for i in range(days):
        day=last-timedelta(days=i);s=utc(str(day));e=add(s,1)
        if not a.covered('commerce',s,e):break
        values.append(money.get(str(day),0))
    return values[::-1],last,a


def quantile(xs,q):
    xs=sorted(xs);i=(len(xs)-1)*q;k=int(i)
    return xs[k]+(xs[min(k+1,len(xs)-1)]-xs[k])*(i-k)


def build_baselines(store,origin=None,horizon=7):
    origin=utc(origin or utc())
    if origin>utc():raise ValueError('Дата прогноза не может быть будущей.')
    if not 1<=horizon<=30:raise ValueError('Горизонт: 1–30 дней.')
    values,last,a=series(store.db,origin)
    if len(values)<35:raise ValueError('Нужно 35 полных дней подтверждённого сбора оплат. Прогноз пока не строится.')
    targets=[dt(origin).date()+timedelta(days=i) for i in range(1,horizon+1)]
    known=[]
    for p in a.placements.values():
        if p['recorded_at']<=origin:
            known.append(dict(id=p['placement_id'],kind='placement',known_at=p['recorded_at'],starts_at=p['published_at']))
    # Include known future plans as well; baselines do not use them as features.
    latest={}
    for v in a.versions:
        if v['recorded_at']<=origin and v['effective_at']<=origin:
            old=latest.get(v['promotion_id'])
            if old is None or (v['effective_at'],v['recorded_at'])>(old['effective_at'],old['recorded_at']):latest[v['promotion_id']]=v
    for v in latest.values():
        if not v['is_cancelled']:known.append(dict(id=v['promotion_id'],kind='promotion',version_id=v['version_id'],known_at=v['recorded_at'],starts_at=v['starts_at']))
    future_ps=store.db.execute('SELECT * FROM placements WHERE recorded_at<=? AND published_at>=?',(origin,origin))
    known += [dict(id=p['placement_id'],kind='placement',known_at=p['recorded_at'],starts_at=p['published_at']) for p in future_ps]
    payload={'forecast_runs':[],'forecast_points':[]}
    for model in ('naive','seasonal_naive'):
        residuals={h:[] for h in range(1,horizon+1)};actuals=[]
        for back in range(horizon+1,horizon+36):
            past=add(origin,-back);ys,ld,old=series(store.db,past)
            if len(ys)<7:continue
            for h in residuals:
                target=dt(past).date()+timedelta(days=h);s=utc(str(target));e=add(s,1)
                if e>origin or not a.covered('commerce',s,e):continue
                actual=sum(p['net_minor'] for p in a.payments if s<=p['occurred_at']<e)
                pred=predict(ys,ld,target,model)
                residuals[h].append(actual-pred);actuals.append(abs(actual))
        errs=[x for xs in residuals.values() for x in xs]
        rid=model+':'+uuid4().hex
        payload['forecast_runs'].append(dict(run_id=rid,model=model,origin_at=origin,created_at=utc(),training_end_at=utc(str(last+timedelta(days=1))),
            horizon_days=horizon,backtest_mae_minor=mean(abs(x) for x in errs) if errs else None,
            backtest_wape=ratio(sum(abs(x) for x in errs),sum(actuals)),backtest_n=len(errs),interval_level=0.8 if all(len(x)>=20 for x in residuals.values()) else None,
            known_campaigns_json=json.dumps(known,ensure_ascii=False),
            notes='Без обучения ML. Ретропрогноз с тогда известными данными. 80% интервал по ошибкам отдельно для каждого горизонта; номинальное покрытие не гарантировано. Кампании перечислены для аудита и не используются этими baseline.'))
        for h,target in enumerate(targets,1):
            value=predict(values,last,target,model);rs=residuals[h]
            payload['forecast_points'].append(dict(point_id=rid+':'+str(target),run_id=rid,day=str(target),value_minor=value,
                lower_minor=value+quantile(rs,0.1) if len(rs)>=20 else None,upper_minor=value+quantile(rs,0.9) if len(rs)>=20 else None))
    return store.load(payload)
