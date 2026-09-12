import argparse
import json
import math
from pathlib import Path
import numpy as np
import joblib
from .contracts import initialize_ml,read_connection,provenance,stamp,Plan
from .decay import configurations
from .search import run_search
from .service import ForecastService,Economics


def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def train(db,out,cutoff,horizon=14,arrival_days=7,max_trials=16,jobs=2,full=False,task='all'):
    from . import customers,channels
    con=read_connection(db)
    try:
        dc=configurations(full);prov=provenance(con);summary={}
        if prov not in ('real','synthetic'):
            raise ValueError('Происхождение данных не подтверждено: используйте app_meta/provenance из импорта или синтетический генератор')
        if task in ('all','purchase'):
            frames,y,meta,quality=customers.training_frames(con,cutoff,horizon,dc)
            a=run_search('purchase',frames,y,meta,dc,out,prov,horizon,max_trials,jobs,full=full)
            summary['purchase']={'quality':quality,'winner':a.metadata['winner'],'test':a.metadata['test_metrics']}
        if task in ('all','ads'):
            frames,y,meta,quality=channels.training_frames(con,cutoff,arrival_days,horizon,dc)
            for target in ('new_users','new_buyers'):
                a=run_search(target,frames,y[target],meta,dc,out,prov,horizon,max_trials,jobs,full=full)
                a.metadata['arrival_days']=arrival_days
                joblib.dump(a,Path(out)/(target+'.joblib'),compress=3)
                summary[target]={'quality':quality,'winner':a.metadata['winner'],'test':a.metadata['test_metrics']}
        Path(out).mkdir(parents=True,exist_ok=True)
        Path(out,'training_summary.json').write_text(json.dumps(clean(summary),ensure_ascii=False,indent=2))
        return summary
    finally:
        con.close()


def main():
    p=argparse.ArgumentParser(description='Модели для БД Поступашки v2')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('init');a.add_argument('--db',required=True)
    a=sub.add_parser('demo');a.add_argument('--db',default='demo.sqlite3');a.add_argument('--seed',type=int,default=42);a.add_argument('--users-per-wave',type=int,default=120)
    a=sub.add_parser('train');a.add_argument('--db',required=True);a.add_argument('--out',default='ml/models');a.add_argument('--cutoff',required=True);a.add_argument('--horizon',type=int,default=14);a.add_argument('--arrival-days',type=int,default=7);a.add_argument('--max-trials',type=int,default=16);a.add_argument('--jobs',type=int,default=2);a.add_argument('--full',action='store_true');a.add_argument('--task',choices=['all','purchase','ads'],default='all')
    for name in ('customers','quote','portfolio','compare'):
        a=sub.add_parser(name);a.add_argument('--db',required=True);a.add_argument('--models',default='ml/models');a.add_argument('--as-of',required=True);a.add_argument('--course',default='ml_course');a.add_argument('--plan');a.add_argument('--out');a.add_argument('--economics')
        if name=='customers':a.add_argument('--csv');a.add_argument('--batch-size',type=int,default=10000)
        if name=='quote':a.add_argument('--channel',required=True);a.add_argument('--expected-views',type=float);a.add_argument('--quoted-price-minor',type=int)
        if name in ('quote','portfolio'):a.add_argument('--incrementality',type=float)
        if name=='portfolio':a.add_argument('--channels',nargs='+',required=True)
    a=p.parse_args()
    if a.command=='init':initialize_ml(a.db);result={'status':'ok','db':a.db}
    elif a.command=='demo':
        from .synthetic import generate
        result=generate(a.db,a.seed,a.users_per_wave)
    elif a.command=='train':result=train(a.db,a.out,a.cutoff,a.horizon,a.arrival_days,a.max_trials,a.jobs,a.full,a.task)
    else:
        plan=json.loads(Path(a.plan).read_text()) if a.plan else None
        economics=Economics(**json.loads(Path(a.economics).read_text())) if a.economics else Economics()
        svc=ForecastService(a.db,a.models)
        if a.command=='customers':result=svc.customers(a.course,a.as_of,plan,batch_size=a.batch_size,economics=economics,out_csv=a.csv)
        elif a.command=='quote':result=svc.quote(a.channel,a.course,a.as_of,plan,expected_views=a.expected_views,incrementality=a.incrementality,economics=economics,quoted_price_minor=a.quoted_price_minor)
        elif a.command=='portfolio':result=svc.portfolio(a.channels,a.course,a.as_of,plan=plan,incrementality=a.incrementality,economics=economics)
        else:
            if not isinstance(plan,list):raise ValueError('Для compare нужен JSON-массив планов')
            result=svc.compare_plans(a.course,a.as_of,plan,economics=economics)
    text=json.dumps(clean(result),ensure_ascii=False,indent=2,allow_nan=False)
    if getattr(a,'out',None) and a.command!='train':
        Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(text)
    print(text)


if __name__=='__main__':main()
