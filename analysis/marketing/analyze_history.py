"""Reproduce the historical price comparison; no attribution or causal estimation."""
from pathlib import Path
from datetime import datetime,timedelta
from decimal import Decimal
from collections import defaultdict,Counter
import argparse
import csv
import hashlib
import json

ROOT=Path(__file__).resolve().parent
OUT=Path('work/marketing')
START_PRODUCTS={'Аналитика старт','Алгоритмы старт','Backend старт','ML старт'}
PRO_AND_AI_PRODUCTS={'Аналитика про','Алгоритмы про','Backend про','ML про','AI агенты'}


def read_orders(path):
    groups=defaultdict(list)
    with path.open(encoding='utf-8-sig',newline='') as f:
        for row_number,r in enumerate(csv.DictReader(f),2):
            t=datetime.strptime(r['Время'],'%d.%m.%Y %H:%M:%S')
            v=Decimal(''.join(r['Сумма'].split()).replace(',','.'))*100
            if v!=v.to_integral_value() or v<=0:raise ValueError('Invalid source amount')
            groups[r['Номер студента'],t].append((r['Курс'],int(v),row_number))
    out=[]
    for (u,t),items in sorted(groups.items()):
        courses=sorted(x[0] for x in items)
        out.append(dict(order_id=hashlib.sha256(f'{u}|{t.isoformat()}'.encode()).hexdigest()[:20],
            user_id=u,date=t.date().isoformat(),timestamp=t.isoformat(),items=len(items),
            courses=courses,amount_cents=sum(x[1] for x in items),source_rows=[x[2] for x in items],
            line_items=[dict(course=x[0],amount_cents=x[1],source_row=x[2]) for x in items]))
    return out


def price_match(o):
    p=o['amount_cents']; cs=o['courses']
    if p==649000 and len(cs)==1 and cs[0] in START_PRODUCTS:return 'price_and_exact_products'
    if p==999000 and len(cs)==2 and len(set(cs))==2 and 'Алгоритмы старт' in cs and set(cs)<=START_PRODUCTS:
        return 'price_and_exact_products'
    if p in (649000,999000):return 'price_only'
    return 'other_amount'


def csv_write(name,rows,columns):
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for r in rows:
            w.writerow({k:json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(list,dict)) else r.get(k) for k in columns})


def period_metrics(orders,start,end):
    q=[o for o in orders if start<=o['date']<=end]
    return dict(start=start,end=end,orders=len(q),buyers=len({o['user_id'] for o in q}),
        sales_rub=sum(o['amount_cents'] for o in q)/100,
        orders_with_pro_or_ai=sum(bool(set(o['courses'])&PRO_AND_AI_PRODUCTS) for o in q),
        pro_or_ai_line_sales_rub=sum(i['amount_cents'] for o in q for i in o['line_items'] if i['course'] in PRO_AND_AI_PRODUCTS)/100)


def match_metrics(orders,start,end):
    q=[dict(o,match_kind=price_match(o)) for o in orders if start<=o['date']<=end]
    result=period_metrics(orders,start,end)
    for label,key in [('strict','price_and_exact_products'),('price_only','price_only'),('other','other_amount')]:
        z=[o for o in q if o['match_kind']==key]
        result[label+'_orders']=len(z)
        result[label+'_sales_rub']=sum(o['amount_cents'] for o in z)/100
    result['strict_single_orders']=sum(o['match_kind']=='price_and_exact_products' and o['items']==1 for o in q)
    result['strict_bundle_orders']=sum(o['match_kind']=='price_and_exact_products' and o['items']==2 for o in q)
    return result,q


def make_calendar(obs,orders,start,end):
    rows=[]; day=datetime.fromisoformat(start)
    while day.date().isoformat()<=end:
        d=day.date().isoformat()
        posts=[p for p in obs if p.get('export_present') and p.get('publication_date')==d]
        sales=[o for o in orders if o['date']==d]
        measured='2026-08-04'<=d<='2026-09-10'
        rows.append(dict(date=d,orders=len(sales) if measured else None,
            sales_rub=sum(o['amount_cents'] for o in sales)/100 if measured else None,
            export_messages=len(posts),export_text_messages=sum(not p['media_only'] for p in posts),
            export_media_only_messages=sum(p['media_only'] for p in posts),
            course_promo_text_messages=sum(p.get('direct_course_promotion',False) for p in posts),
            free_week_text_messages=sum('free_week' in p.get('campaign_ids',[]) for p in posts),
            third_party_promo_text_messages=sum(p.get('content_kind')=='third_party_promotion' for p in posts),
            tbank_course_promo_text_messages=sum('tbank_selection' in p.get('campaign_ids',[]) and p.get('direct_course_promotion',False) for p in posts),
            identified_start_sale_day=int('2026-08-08'<=d<='2026-08-10'),
            identified_free_week_day=int('2026-09-07'<=d<='2026-09-13'),
            pro_announcement_day=int(d=='2026-08-22'),tbank_deadline_in_posts=int(d=='2026-09-06'),
            total_external_placements=None,ad_spend_rub=None,
            calendar_coverage='export_observed_lower_bound',
            date_basis='posts_source_UTC+03; sales_source_local_timezone_unknown'))
        day+=timedelta(days=1)
    return rows


def main():
    global OUT
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--out',type=Path,default=OUT)
    args=p.parse_args(); OUT=args.out
    OUT.mkdir(parents=True,exist_ok=True)
    evidence=json.loads((ROOT/'observations.json').read_text(encoding='utf-8'))
    obs=evidence['observations']; orders=read_orders(args.input)
    assert len(orders)==628 and sum(o['items'] for o in orders)==795
    assert sum(o['amount_cents'] for o in orders)==590467167
    original,old_rows=match_metrics(orders,'2026-08-08','2026-08-09')
    extended,selected=match_metrics(orders,'2026-08-08','2026-08-10')
    extra,extra_rows=match_metrics(orders,'2026-08-10','2026-08-10')
    periods=[]
    for start,end,status,source in [
        ('2026-08-08','2026-08-10','sale_extended_through_aug10','https://t.me/postypashki_old/1838'),
        ('2026-08-22','2026-08-23','dated_pro_announcement_same_calendar_period','https://t.me/postypashki_old/1859'),
        ('2026-09-05','2026-09-06','tbank_deadline_and_course_promotion_context','https://t.me/postypashki_old/1880')]:
        periods.append(dict(period_metrics(orders,start,end),evidence_status=status,source_url=source))
    launch=next(p for p in obs if p['observation_id']=='postypashki_old:1859')
    extension=next(p for p in obs if p['observation_id']=='postypashki_old:1838')
    # This is explicitly a sensitivity scenario, not a timezone assumption in the core calculations.
    ext_time=datetime.fromisoformat(extension['published_at_source']).replace(tzinfo=None)
    pre_extension=[o for o in extra_rows if o['match_kind']=='price_and_exact_products' and datetime.fromisoformat(o['timestamp'])<ext_time]
    launch_time=datetime.fromisoformat(launch['published_at_source']).replace(tzinfo=None)
    launch_day=[o for o in orders if o['date']=='2026-08-22']
    conditional=dict(assumption='ONLY_IF_sales_timestamps_are_also_UTC+03',
        strict_aug10_orders_before_extension_post=len(pre_extension),
        strict_aug10_sales_before_extension_post_rub=sum(o['amount_cents'] for o in pre_extension)/100,
        aug22_orders_before_launch_post=sum(datetime.fromisoformat(o['timestamp'])<launch_time for o in launch_day),
        aug22_total_orders=len(launch_day))
    export=[p for p in obs if p.get('export_present')]
    summary=dict(source_sha256=hashlib.sha256((args.input).read_bytes()).hexdigest(),
        observation_count=len(obs),export_source_message_count=evidence['source_message_count'],
        export_period_messages=len(export),export_period_text_messages=evidence['selected_text_messages'],
        export_period_media_only_messages=evidence['selected_media_only_messages'],
        export_sales_period_messages=sum('2026-08-04'<=p['publication_date']<='2026-09-10' for p in export),
        complete_publication_timestamps=sum(bool(p.get('published_at')) for p in obs),
        known_main_channel_posts_missing_from_export=evidence['known_main_channel_posts_missing_from_export'],
        direct_course_promotion_messages=sum(p.get('direct_course_promotion',False) for p in export),
        third_party_promotion_messages=sum(p['content_kind']=='third_party_promotion' for p in export),
        all_orders=len(orders),all_sales_rub=sum(o['amount_cents'] for o in orders)/100,
        original_sale_window=original,extended_sale_window=extended,extension_day=extra,
        periods=periods,conditional_intraday=conditional,
        price_6490_orders_outside_extended_window=sum(o['amount_cents']==649000 and not '2026-08-08'<=o['date']<='2026-08-10' for o in orders),
        price_9990_orders_outside_extended_window=sum(o['amount_cents']==999000 and not '2026-08-08'<=o['date']<='2026-08-10' for o in orders),
        limitations=['known_posts_missing_from_export','no_media_contents','no_buyer_touches','no_marketing_costs',
            'sales_timezone_unknown','no_historical_post_revision_log','not_causal'])
    campaigns=[
        dict(campaign_id='start_sale_august',start='2026-08-08',end='2026-08-10',
            original_end='2026-08-09',evidence_posts=[1835,1836,1837,1838,1840],
            date_basis='dated_reminders_and_extension; original_terms_from_prior_web_review',
            pricing_basis='6490_single_START;9990_distinct_START_pair_including_Algorithms_START',
            known_at='2026-08-10T16:26:59+03:00',note='Продление известно с поста 1838, нельзя использовать его как заранее известный признак.'),
        dict(campaign_id='pro_launch_aug22',start='2026-08-22',end=None,original_end=None,evidence_posts=[1859,1860,1863],
            date_basis='announcement_timestamp; actual_course_start_not_stated',pricing_basis='not_stated',
            known_at=launch['published_at_source'],note='Дата анонса, не доказанный первый день продаж или занятий.'),
        dict(campaign_id='tbank_selection',start=None,end='2026-09-06',original_end=None,
            evidence_posts=[1870,1872,1875,1876,1877,1878,1880,1881,1882,1885],
            date_basis='deadline_as_stated_in_channel_posts',pricing_basis='not_stated',
            known_at='2026-08-28T19:05:59+03:00',note='Внешний дедлайн используется для предложения своих курсов; расходы и касания неизвестны.'),
        dict(campaign_id='yandex_selection',start=None,end=None,original_end=None,evidence_posts=[1870,1872,1882,1895],
            date_basis='dated_posts; exact_selection_window_unknown',pricing_basis='not_stated',
            known_at='2026-08-28T19:05:59+03:00',note='Разборы контеста на курсах; не установлен полный календарь набора.'),
        dict(campaign_id='ai_webinar',start=None,end=None,original_end=None,evidence_posts=[1874],
            date_basis='recording_promotion_date_only',pricing_basis='promo_code_amount_and_validity_unknown',
            known_at='2026-08-30T16:26:37+03:00',note='Дата публикации записи не равна дате вебинара.'),
        dict(campaign_id='free_week',start='2026-09-07',end='2026-09-13',original_end=None,
            evidence_posts=[1887,1892,1893,1897,1898,1900],
            date_basis='explicit_window_and_dated_reminders',pricing_basis='free_participation; prize_not_ad_spend',
            known_at='2026-09-07T13:47:40+03:00',note='Продажи заканчиваются 10 сентября; результат всей недели ещё не наблюдается.')]
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    cols=['observation_id','source_url','evidence_url','provenance','review_status','export_present',
        'content_kind','marketing_target','published_at','published_at_source','publication_date','publication_date_status',
        'media_only','has_media','direct_course_promotion','campaign_ids','activity_start','activity_end','activity_date_basis',
        'summary','products','advertised_prices_rub','marketing_cost_rub','text_sha256','links']
    csv_write('observations.csv',obs,cols)
    sales_cols=['order_id','user_id','timestamp','courses','items','amount_cents','source_rows','match_kind']
    # Customer-level price comparisons remain in memory.

    csv_write('peak_periods.csv',periods,list(periods[0]))
    csv_write('campaigns.csv',campaigns,list(campaigns[0]))
    daily=make_calendar(obs,orders,'2026-08-04','2026-09-10')
    all_days=make_calendar(obs,orders,'2026-07-28','2026-09-11')
    csv_write('daily_calendar.csv',daily,list(daily[0]))
    csv_write('post_calendar.csv',all_days,list(all_days[0]))
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
