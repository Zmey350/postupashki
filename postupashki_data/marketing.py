"""Статистика на дату: наблюдаемые маршруты и связанные продажи, не causal uplift."""

import csv
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from .db import utc
from .features import _date, _days, _group, _covered


def _rows(con, table, as_of, time=None):
    sql = f'SELECT * FROM {table} WHERE recorded_at<=?'
    args = [as_of]
    if time:
        sql += f' AND {time}<?'
        args.append(as_of)
    return [dict(r) for r in con.execute(sql, args)]


def _end(start, days):
    return utc(_date(start) + timedelta(days=days))


def _ratio(a, b):
    return a / b if b else None


class History:
    def __init__(self, con, as_of):
        self.at = as_of
        self.periods = defaultdict(list)
        for row in sorted(_rows(con, 'collection_periods', as_of), key=lambda r:r['starts_at']):
            self.periods[row['domain']].append((row['starts_at'],row['ends_at']))
        self.users = {r['user_id']:r for r in _rows(con,'users',as_of,'first_seen_at')}
        self.orders = {r['order_id']:r for r in _rows(con,'orders',as_of,'ordered_at')}
        self.orders_by_user = _group(self.orders.values(),'user_id')
        self.payments = _group(_rows(con,'payments',as_of,'occurred_at'),'order_id')
        self.items = _group(con.execute('SELECT * FROM order_items'),'order_id')
        self.member = _group(sorted(_rows(con,'membership_events',as_of,'occurred_at'),
                            key=lambda r:(r['occurred_at'],r['recorded_at'],r['event_id'])),'user_id')

    def covered(self, domain, start, end):
        return _covered(self.periods, domain, start, end)

    def ledger(self, ids, start, end):
        cash, fees, costs, paid, complete_costs = 0, 0, 0, [], True
        for oid in ids:
            order = self.orders.get(oid)
            if order is None or not start <= order['ordered_at'] < end:
                continue
            payments = [p for p in self.payments[oid] if p['occurred_at'] < end]
            deltas = defaultdict(int)
            for p in payments:
                delta = p['amount_minor'] * (1 if p['kind']=='payment' else -1)
                deltas[p['occurred_at']] += delta
                cash += delta
                if p['fee_minor'] is None:
                    complete_costs = False
                else:
                    fees += p['fee_minor']
            balance, completed = 0, False
            for when, delta in sorted(deltas.items()):
                balance += delta
                if order['total_minor'] > 0 and balance >= order['total_minor']:
                    completed = True
            if completed:
                paid.append(oid)
                for item in self.items[oid]:
                    if item['variable_cost_minor'] is None:
                        complete_costs = False
                    else:
                        costs += item['variable_cost_minor']
            elif balance:
                # Аванс ещё не является маржинальным доходом завершённого заказа.
                complete_costs = False
        return dict(cash=cash, paid=paid, contribution=cash-fees-costs if complete_costs else None)

    def user_orders(self, uid):
        return [r['order_id'] for r in self.orders_by_user[uid]]

    def membership(self, uid, channel, start, end):
        events = [r for r in self.member[uid] if r['channel_id']==channel and r['occurred_at']<end]
        state = None
        joined, left, banned, rejoined = False, False, False, False
        first_join, prev, has_exit = None, None, False
        for r in events:
            if r['occurred_at'] < start:
                prev = r
                continue
            if prev and not self.covered('membership',prev['occurred_at'],r['occurred_at']):
                prev = None
            if prev and prev['status']=='joined':
                joined = True
                first_join = first_join or start
            if r['status']=='joined':
                joined = True
                first_join = first_join or r['occurred_at']
                if has_exit and prev and prev['status']!='joined' and r['evidence_kind']=='status_update':
                    rejoined = True
            if r['evidence_kind']=='status_update' and prev and prev['status']=='joined':
                if r['status']=='left':
                    left, has_exit = True, True
                if r['status']=='banned':
                    banned, has_exit = True, True
            prev = r
        if prev and self.covered('membership',prev['occurred_at'],end):
            state = int(prev['status']=='joined')
            if state:
                joined = True
                first_join = first_join or start
        eligible = bool(joined and first_join is not None and state is not None
                        and self.covered('membership',first_join,end))
        return dict(joined=joined,left=left,banned=banned,rejoined=rejoined,state=state,eligible=eligible)


def _channel_rows(con, h, horizon, main):
    at = h.at
    channels = {r['channel_id']:r for r in _rows(con,'channels',at,'created_at') if r['kind']=='external'}
    all_events = sorted(_rows(con,'acquisition_events',at,'occurred_at'),
                        key=lambda r:(r['occurred_at'],r['recorded_at'],r['event_id']))
    all_events = [r for r in all_events if r['user_id'] in h.users]
    first = {uid:rows[0] for uid,rows in _group(all_events,'user_id').items()}
    # Пользователь без установленного первого источника остаётся в отдельной строке.
    unknown = '__unknown__'
    if unknown in channels:
        raise ValueError('__unknown__ зарезервирован для неизвестного источника.')
    channels[unknown] = dict(topic_id=None)
    cohorts = defaultdict(list)
    for uid,u in h.users.items():
        event = first.get(uid)
        key = event['source_channel_id'] if event else None
        if key not in channels:
            key = unknown
        cohorts[key].append((uid,event['occurred_at'] if event else u['first_seen_at']))
    placements = _rows(con,'placements',at,'published_at')
    costs = _group(_rows(con,'placement_cost_events',at,'occurred_at'),'placement_id')
    snapshots = {}
    for r in sorted(_rows(con,'channel_snapshots',at,'observed_at'),
                    key=lambda r:(r['observed_at'],r['recorded_at'],r['snapshot_id'])):
        snapshots[r['channel_id']] = r
    for cid,c in channels.items():
        cohort = cohorts[cid]
        touched = [r for r in all_events if r['source_channel_id']==cid]
        ads = [r for r in placements if r['channel_id']==cid]
        snap = snapshots.get(cid,{})
        mature = [(uid,s) for uid,s in cohort if _end(s,horizon)<=at]
        observed = [(uid,s) for uid,s in mature if h.covered('commerce',h.users[uid]['first_seen_at'],_end(s,horizon))]
        horizon_money = [h.ledger(h.user_orders(uid),s,_end(s,horizon)) for uid,s in observed]
        coverage_starts = [h.users[uid]['first_seen_at'] for uid,s in cohort] + [p['published_at'] for p in ads]
        acquisition_complete = bool(coverage_starts) and h.covered('acquisition',min(coverage_starts),at)
        full_horizon = len(mature)==len(observed) and acquisition_complete
        all_commerce = all(h.covered('commerce',h.users[uid]['first_seen_at'],at) for uid,s in cohort)
        full_money = [h.ledger(h.user_orders(uid),s,at) for uid,s in cohort]
        net = sum(r['cash'] for r in full_money) if all_commerce and acquisition_complete else None
        contribution = sum(r['contribution'] for r in full_money) if net is not None and all(r['contribution'] is not None for r in full_money) else None
        missing_costs = [p['placement_id'] for p in ads if not costs[p['placement_id']]]
        cost_starts = [min(p['published_at'],p['recorded_at']) for p in ads] + [r['occurred_at'] for p in ads for r in costs[p['placement_id']]]
        cost_complete = bool(cost_starts) and h.covered('ad_costs',min(cost_starts),at)
        cost = sum(r['amount_minor']*(1 if r['kind']=='expense' else -1) for p in ads for r in costs[p['placement_id']]) if cost_complete else None
        # ROMI только для отслеживаемого платного привлечения этой когорты.
        attribution_covered = all(first.get(uid,{}).get('placement_id') in {p['placement_id'] for p in ads} for uid,s in cohort)
        romi = (contribution-cost)/cost if cost and contribution is not None and attribution_covered else None
        state = [h.membership(uid,main,s,at) for uid,s in cohort] if main else []
        retention = [h.membership(uid,main,s,_end(s,horizon)) for uid,s in mature] if main else []
        retained = [r for r in retention if r['eligible']]
        row = dict(channel_id=cid,as_of=at,horizon_days=horizon,main_channel_id=main or '',
            topic_id=c['topic_id'],audience_stage=snap.get('audience_stage'),subscribers=snap.get('subscribers'),
            typical_post_views_24h=snap.get('typical_post_views_24h'),
            snapshot_age_days=_days(at,snap['observed_at']) if snap else None,
            placements_count=len(ads),days_since_last_placement=_days(at,max(p['published_at'] for p in ads)) if ads else None,
            touch_users=len({r['user_id'] for r in touched}),first_source_users=len(cohort),
            returning_touch_users=len({r['user_id'] for r in touched if r['event_id']!=first[r['user_id']]['event_id']}),
            mature_users=len(mature),commerce_observed_users=len(observed),
            buyers_horizon=sum(bool(r['paid']) for r in horizon_money),
            conversion_horizon=_ratio(sum(bool(r['paid']) for r in horizon_money),len(observed)) if full_horizon else None,
            net_cash_horizon_minor=sum(r['cash'] for r in horizon_money) if full_horizon else None,
            cash_per_user_horizon_minor=_ratio(sum(r['cash'] for r in horizon_money),len(observed)) if full_horizon else None,
            member_observed_users=sum(r['joined'] for r in state) if main else None,
            left_users=sum(r['left'] for r in state) if main else None,
            banned_users=sum(r['banned'] for r in state) if main else None,
            rejoined_users=sum(r['rejoined'] for r in state) if main else None,
            current_members=sum(r['state']==1 for r in state) if main else None,
            current_membership_unknown_users=sum(r['state'] is None for r in state) if main else None,
            retention_eligible_users=len(retained) if main else None,
            retention_horizon=_ratio(sum(r['state']==1 for r in retained),len(retained)) if acquisition_complete else None,
            net_cash_to_date_minor=net,contribution_to_date_minor=contribution,ad_cost_minor=cost,
            cost_per_first_source_user_minor=_ratio(cost,len(cohort)) if cost is not None and attribution_covered and acquisition_complete else None,
            attributed_romi=romi,
            quality_json=json.dumps(dict(acquisition_complete=acquisition_complete,commerce_complete=all_commerce,
                placements_without_cost_events=missing_costs,ad_costs_complete=cost_complete,cost_basis='expenses minus refunds; requires ad_costs coverage',
                counters='observed users; lower bounds if collection is incomplete',
                attribution='first observed source; unknown/own/unresolved grouped as __unknown__; no causal effect',
                horizons='fixed days from first acquisition; only mature users; payments and refunds inside window',
                retention='known members in selected own_main at horizon; denominator retention_eligible_users',
                romi='cash contribution since acquisition / all recorded channel ad costs - 1; descriptive, not incremental'),ensure_ascii=False))
        yield row


def _promotion_rows(con, h, horizon):
    at = h.at
    promotions = _rows(con,'promotions',at,'created_at')
    versions = {r['version_id']:r for r in _rows(con,'promotion_versions',at) if r['effective_at']<=at}
    latest = {}
    for v in sorted(versions.values(),key=lambda r:(r['effective_at'],r['recorded_at'],r['version_id'])):
        latest[v['promotion_id']] = v
    courses = _group(con.execute('SELECT * FROM promotion_courses'),'version_id')
    offers = {r['offer_id']:r for r in _rows(con,'offers',at,'sent_at') if r['user_id'] in h.users}
    offer_links = {r['offer_id']:r for r in _rows(con,'promotion_offers',at) if r['version_id'] in versions and r['offer_id'] in offers}
    responses = _rows(con,'promotion_responses',at,'occurred_at')
    orders = [r for r in _rows(con,'order_promotions',at) if r['version_id'] in versions and r['order_id'] in h.orders]
    for promo in promotions:
        pid = promo['promotion_id']
        v = latest.get(pid)
        links = {oid:x for oid,x in offer_links.items() if versions[x['version_id']]['promotion_id']==pid}
        sent = sorted([offers[oid] for oid in links if offers[oid]['status']=='sent'],key=lambda r:(r['sent_at'],r['offer_id']))
        by_user = _group(sent,'user_id')
        mature = [(uid,rs[0]['sent_at']) for uid,rs in by_user.items() if _end(rs[0]['sent_at'],horizon)<=at]
        observed = [(uid,s) for uid,s in mature if h.covered('commerce',h.users[uid]['first_seen_at'],_end(s,horizon))]
        linked = [r for r in orders if versions[r['version_id']]['promotion_id']==pid]
        ids = [r['order_id'] for r in linked]
        money = h.ledger(ids,promo['created_at'],at)
        buyers_horizon = sum(bool(h.ledger([oid for oid in ids if h.orders[oid]['user_id']==uid],s,_end(s,horizon))['paid']) for uid,s in observed)
        clicked = {offers[r['offer_id']]['user_id'] for r in responses if r['kind']=='clicked' and r['offer_id'] in links and offers[r['offer_id']]['status']=='sent'}
        full_offers = all(h.covered(d,promo['created_at'],at) for d in ('offers','promotion_offers'))
        full_responses = h.covered('promotion_responses',promo['created_at'],at)
        full_links = h.covered('order_promotions',promo['created_at'],at)
        row = dict(promotion_id=pid,as_of=at,horizon_days=horizon,version_id=v['version_id'] if v else None,kind=promo['kind'],
            starts_at=v['starts_at'] if v else None,ends_at=v['ends_at'] if v else None,
            is_active=int(v['starts_at']<=at<v['ends_at'] and not v['is_cancelled']) if v else None,
            duration_days=_days(v['ends_at'],v['starts_at']) if v else None,
            days_to_end=_days(v['ends_at'],at) if v else None,
            discount_type=v['discount_type'] if v else None,discount_value=v['discount_value'] if v else None,
            courses_count=len(courses[v['version_id']]) if v else None,
            sent_offers=len(sent),sent_users=len(by_user),failed_offers=sum(offers[oid]['status']=='failed' for oid in links),
            clicked_users=len(clicked),click_rate=_ratio(len(clicked),len(by_user)) if full_offers and full_responses else None,
            mature_sent_users=len(mature),commerce_observed_users=len(observed),buyers_horizon=buyers_horizon,
            conversion_horizon=_ratio(buyers_horizon,len(observed)) if len(observed)==len(mature) and full_offers and full_links else None,
            linked_paid_orders=len(money['paid']),linked_buyers=len({h.orders[oid]['user_id'] for oid in money['paid']}),
            net_cash_minor=money['cash'],mean_discount_sent_pct=_ratio(sum(100*(1-r['offered_price_minor']/r['regular_price_minor']) for r in sent),len(sent)),
            repeat_sent_users=sum(len(rs)>1 for rs in by_user.values()),
            quality_json=json.dumps(dict(offers_complete=full_offers,responses_complete=full_responses,order_links_complete=full_links,
                linked_sales='explicit primary promotion per order; not incremental sales',
                counters='observed, possibly incomplete; see separate marketing collection domains',
                assignment_counts=dict(Counter(links[r['offer_id']]['assignment_reason'] for r in sent)),
                horizons='from first successful send; linked paid orders within fixed window; no reading inference'),ensure_ascii=False))
        yield row


def build_marketing(con, as_of, horizon_days=30, main_channel_id=None):
    as_of = utc(as_of)
    if as_of > utc():
        raise ValueError('Дата расчёта не может быть будущей.')
    if not isinstance(horizon_days,int) or isinstance(horizon_days,bool) or horizon_days<=0:
        raise ValueError('horizon_days должен быть положительным целым.')
    main = [r['channel_id'] for r in _rows(con,'channels',as_of,'created_at') if r['kind']=='own_main']
    if main_channel_id is not None and main_channel_id not in main:
        raise ValueError('Выберите существующий на дату расчёта own_main канал.')
    if main_channel_id is None and len(main)>1:
        raise ValueError('Несколько основных каналов: укажите --main-channel для статистики отписок.')
    main_channel_id = main_channel_id or (main[0] if main else None)
    counts = {}
    with con:
        con.execute('BEGIN IMMEDIATE')
        h = History(con,as_of)
        for table, rows in [('channel_stats',_channel_rows(con,h,horizon_days,main_channel_id)),
                            ('promotion_stats',_promotion_rows(con,h,horizon_days))]:
            sql = f'DELETE FROM {table} WHERE as_of=? AND horizon_days=?'
            args = [as_of,horizon_days]
            if table=='channel_stats':
                sql += ' AND main_channel_id=?'
                args.append(main_channel_id or '')
            con.execute(sql,args)
            n = 0
            for r in rows:
                con.execute(f'INSERT INTO {table} ({",".join(r)}) VALUES ({",".join("?" for _ in r)})',list(r.values()))
                n += 1
            counts[table] = n
    return counts


def export_marketing(con, table, as_of, path, horizon_days=30, main_channel_id=None):
    if table not in ('channel_stats','promotion_stats'):
        raise ValueError('Ожидается channel_stats или promotion_stats.')
    args = [utc(as_of),horizon_days]
    sql = f'SELECT * FROM {table} WHERE as_of=? AND horizon_days=?'
    if table=='channel_stats' and main_channel_id is not None:
        sql += ' AND main_channel_id=?'
        args.append(main_channel_id)
    sql += ' ORDER BY 1'
    cur = con.execute(sql,args)
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow([r[0] for r in cur.description])
        writer.writerows(cur)
    return str(path)
