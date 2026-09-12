"""Five attribution rules on isolated real/synthetic data; reproducible, stdlib only."""
from pathlib import Path
from collections import defaultdict,Counter
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
from fractions import Fraction
import argparse,csv,hashlib,json,sys,tempfile

R=Path(__file__).resolve().parent
sys.path.insert(0,str(R/'engine'))
from mvp import Store,MODELS,import_sales,export_report

WINDOWS=(1,3,7,14,21)
DEMO_END='2026-09-15T00:00:00Z'  # Synthetic scenario clock; not future actual sales.
REAL_END='2026-09-11T00:00:00'
HISTORY_END='2026-09-12T00:00:00Z'


def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def csv_write(path,rows,columns=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    if columns is None: columns=list(rows[0]) if rows else []
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(row.get(k),ensure_ascii=False) if isinstance(row.get(k),(list,dict)) else row.get(k) for k in columns})


def overview(report):
    t=report['totals']
    return dict(dataset_id=report['dataset']['id'],provenance=report['dataset']['provenance'],
        sales_policy=report['dataset']['sales_policy'],model=report['model'],window_days=report['window_days'],
        half_life_days=report['half_life_days'],sales_rub=t['sales_cents']/100,
        attributed_rub=t['attributed_cents']/100,unknown_source_rub=t['unattributed_cents']/100,
        coverage_pct=t['coverage_pct'],paid_orders=t['paid_orders'],buyers=t['buyers'],
        successful_records=t['successful_payments'],romi_pct=t['romi_pct'],cost_complete=t['cost_complete'])


def movement(a,b):
    def index(r):
        z=defaultdict(int)
        for x in r['allocations']:z[x['payment_id'],x['placement_id']]+=x['amount_cents']
        for x in r['unattributed_payments']:z[x['payment_id'],'__unknown_source__']+=x['amount_cents']
        return z
    x,y=index(a),index(b);keys=set(x)|set(y)
    moved=sum(abs(x[k]-y[k]) for k in keys)//2
    return dict(reallocated_rub=moved/100,reallocated_share_of_sales_pct=100*moved/a['totals']['sales_cents'],
        changed_payment_records=len({k[0] for k in keys if x[k]!=y[k]}))


def payment_paths(store,ds,as_of,days):
    _,end=store.stamp(ds,as_of);rows=[]
    query="SELECT p.*,o.user_key FROM payments p JOIN orders o ON p.dataset_id=o.dataset_id AND p.order_id=o.id WHERE p.dataset_id=? AND p.status='succeeded' AND p.occurred_us<=? ORDER BY p.occurred_us,p.id"
    for p in store.db.execute(query,(ds,end)):
        ts=list(store.db.execute('SELECT * FROM touches WHERE dataset_id=? AND user_key=? AND occurred_us<=? ORDER BY occurred_us,id',(ds,p['user_key'],p['occurred_us'])))
        selected=[t for t in ts if t['occurred_us']>=p['occurred_us']-days*86400*1000000]
        reason='has_observed_touch_in_window' if selected else 'only_older_observed_touches' if ts else 'no_observed_touch_before_payment'
        rows.append(dict(dataset_id=ds,provenance=store.dataset(ds)['provenance'],payment_id=p['id'],
            order_id=p['order_id'],user_key=p['user_key'],occurred_at=p['occurred_at'],evidence=p['evidence'],
            amount_cents=p['amount_cents'],amount_rub=p['amount_cents']/100,window_days=days,reason=reason,
            observed_path=[{'event_id':t['id'],'placement_id':t['placement_id'],'event_type':t['event_type'],'occurred_at':t['occurred_at'],
                'age_days':(p['occurred_us']-t['occurred_us'])/(86400*1000000)} for t in selected]))
    return rows


def history_payload():
    history=json.loads((R.parent/'marketing/observations.json').read_text(encoding='utf-8'))
    records=[];mapping=[]
    for post in history['observations']:
        if not post.get('export_present') or post.get('media_only') or post.get('marketing_target') not in ('own_product','own_audience'):
            continue
        pid='tg_'+str(post['post_id'])
        records.append(dict(type='placement',id=pid,campaign_id='owned_history',creative_id='post_'+str(post['post_id']),
            channel='@postypashki_old',kind='owned',publication_time=post['published_at'],cost=None))
        mapping.append(dict(placement_id=pid,source_url=post['source_url'],observation_id=post['observation_id'],
            published_at_source=post['published_at_source'],content_kind=post['content_kind'],
            campaign_topics=post.get('campaign_ids',[]),direct_course_promotion=post.get('direct_course_promotion',False),
            cost_rub=None,provenance='real',summary=post['summary']))
    payload={'dataset':{'id':'history_publications_2026','label':'Публичные посты: даты известны, касаний и оплат нет',
        'provenance':'real','clock':'UTC','sales_policy':'recorded_payments'},'records':records}
    return payload,mapping


def planned_template():
    rows=[]
    for i in range(1,21):
        rows.append(dict(status='planned_template_not_loaded',campaign_id='future_campaign',placement_id=f'future_p{i:02}',
            creative_id=f'future_c{i:02}',channel_url=None,publication_time=None,cost_rub=None,
            tracking_token=f'future_p{i:02}',actual_post_url=None,owner=None))
    return rows


def run(out,db,source):
    out.mkdir(parents=True,exist_ok=True)
    demo=json.loads((R/'data/demo.json').read_text(encoding='utf-8'))
    history,mapping=history_payload()
    dump(out/'history_placements_import.json',history)
    csv_write(out/'future_20_placements.csv',planned_template())
    rows=[];placement_rows=[];ledger=[];saved={}
    with Store(db) as store:
        real_import=import_sales(store,source)
        store.load(demo);registry_import=store.load(history)
        for row in mapping:
            row['tracking_token']=store.get('placements','history_publications_2026',row['placement_id'])['token']
        csv_write(out/'history_placements.csv',mapping)
        no_b=deepcopy(demo);no_b['dataset']['id']='demo_missing_b_clicks'
        no_b['dataset']['label']='Синтетический сценарий: потеря двух касаний Б'
        no_b['records']=[r for r in no_b['records'] if r['id'] not in ('t1b','t7b')]
        store.load(no_b)
        for ds,end in [('demo_2026',DEMO_END),('sales_2026',REAL_END)]:
            for window in WINDOWS:
                for model in MODELS:
                    report=store.report(ds,end,model,window)
                    saved[ds,model,window]=report;rows.append(overview(report))
                    for p in report['placements']:
                        placement_rows.append(dict(dataset_id=ds,provenance=report['dataset']['provenance'],model=model,
                            window_days=window,placement_id=p['placement_id'],channel=p['channel'],
                            cost_rub=None if p['cost_cents'] is None else p['cost_cents']/100,
                            attributed_rub=p['attributed_cents']/100,romi_pct=p['romi_pct'],romi_status=p['romi_status']))
                    if ds=='demo_2026':
                        for p in report['allocations']:
                            ledger.append(dict(dataset_id=ds,provenance='synthetic',model=model,window_days=window,
                                payment_id=p['payment_id'],order_id=p['order_id'],placement_id=p['placement_id'],
                                amount_cents=p['amount_cents'],
                                weight=str(Decimal(p['weight_numerator'])/Decimal(p['weight_denominator']))))
            if ds=='sales_2026':dump(out/'real_last_touch_7.json',overview(saved[ds,'last_touch',7]))
        for model in MODELS:export_report(saved['demo_2026',model,7],out/f'demo_{model}_7')
        hreport=store.report('history_publications_2026',HISTORY_END)
        export_report(hreport,out/'history_registry')
        demo_paths=payment_paths(store,'demo_2026',DEMO_END,7)
        real_paths=payment_paths(store,'sales_2026',REAL_END,7)
        csv_write(out/'demo_payment_paths_7.csv',demo_paths)
        # Individual historic sales never appear in public output.
        ablation=store.report('demo_missing_b_clicks',DEMO_END)
        export_report(ablation,out/'demo_missing_b_clicks_7')
        baseline=saved['demo_2026','last_touch',7]
        stability=[dict(model=m,reference='last_touch',window_days=7,**movement(baseline,saved['demo_2026',m,7])) for m in MODELS]
        halves=[]
        for half in (1,3,7):
            r=store.report('demo_2026',DEMO_END,'time_decay',7,half)
            for p in r['placements']:halves.append(dict(provenance='synthetic',half_life_days=half,placement_id=p['placement_id'],attributed_rub=p['attributed_cents']/100))
        # Re-import cannot turn post metadata into user events or add revenue.
        again=store.load(history)
        assert again['created_records']==0
        assert hreport['registered_records']['touches']==0 and hreport['totals']['sales_cents']==0
        real=saved['sales_2026','last_touch',7]
        assert real['totals']['sales_cents']==590467167 and real['totals']['unattributed_cents']==590467167
        assert real['totals']['attributed_cents']==0 and real['totals']['paid_orders']==628 and real['totals']['buyers']==606
        for ds in ('demo_2026','sales_2026'):
            for w in WINDOWS:
                rr=[saved[ds,m,w] for m in MODELS]
                assert len({r['totals']['attributed_cents'] for r in rr})==1
                for r in rr:
                    p=defaultdict(int)
                    for a in r['allocations']:p[a['payment_id']]+=a['amount_cents']
                    for a in r['unattributed_payments']:p[a['payment_id']]+=a['amount_cents']
                    for a in payment_paths(store,ds,DEMO_END if ds=='demo_2026' else REAL_END,w):
                        assert p[a['payment_id']]==a['amount_cents']
        summary=dict(real=overview(real),demo=overview(baseline),source_import=real_import,
            model_count=len(MODELS),windows_days=list(WINDOWS),comparison_runs=len(rows),
            demo_stability=stability,demo_window_comparison=[overview(saved['demo_2026','last_touch',w]) for w in WINDOWS],
            real_all_models_and_windows_have_zero_coverage=True,
            history_registry=dict(placements=len(mapping),paid_course_promotions=sum(p['direct_course_promotion'] for p in mapping),
                distinct_channels=1,costs_known=0,observed_touches=0,observed_payments=0,reimport_created_records=again['created_records'],
                clock='UTC',legacy_sales_clock='local_unspecified'),
            real_unknown_reasons=dict(Counter(p['reason'] for p in real_paths)),
            demo_unknown_reasons={reason:{'records':sum(p['reason']==reason for p in demo_paths),
                'sales_rub':sum(p['amount_rub'] for p in demo_paths if p['reason']==reason)} for reason in sorted({p['reason'] for p in demo_paths})},
            demo_missing_clicks=dict(removed_event_ids=['t1b','t7b'],original_coverage_pct=baseline['totals']['coverage_pct'],
                changed_coverage_pct=ablation['totals']['coverage_pct'],**movement(baseline,ablation),
                placement_revenue_rub={p['placement_id']:p['attributed_cents']/100 for p in ablation['placements']}),
            working_policy={'model':'last_touch','window_days':7,'basis':'operational_starting_rule; not empirically optimal',
                'alternatives':['first_touch','linear','time_decay_half_life_3_days','position_based_40_20_40'],
                'no_real_channel_ranking':True,'attribution_is_not_incrementality':True})
        dump(out/'summary.json',summary)
    csv_write(out/'model_window_comparison.csv',rows)
    csv_write(out/'demo_placement_comparison.csv',placement_rows)
    csv_write(out/'demo_allocation_ledger.csv',ledger)
    csv_write(out/'demo_model_stability.csv',stability)
    csv_write(out/'demo_decay_sensitivity.csv',halves)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    a=argparse.ArgumentParser();a.add_argument('--out',type=Path,default=R/'results')
    a.add_argument('--db',type=Path,help='Create or reuse a persistent SQLite database; default: isolated temporary database')
    a.add_argument('--input',type=Path,required=True,help='Original owner-supplied CSV; kept outside repository')
    x=a.parse_args()
    if x.db:run(x.out,x.db,x.input)
    else:
        with tempfile.TemporaryDirectory() as t:run(x.out,Path(t)/'analysis.sqlite3',x.input)


if __name__=='__main__':main()
