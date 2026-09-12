"""Point-in-time расчёт признаков: occurred_at < as_of, recorded_at <= as_of."""

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from .catalog import FEATURE_NAMES
from .db import utc


def _date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _days(end, start):
    return (_date(end) - _date(start)).total_seconds() / 86400


def _group(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[row[key]].append(dict(row))
    return result


def _covered(periods, domain, start, end):
    cursor = start
    for left, right in periods.get(domain, []):
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = max(cursor, right)
        if cursor >= end:
            return True
    return cursor >= end


def _compute(con, as_of):
    cutoffs = {n: utc(_date(as_of) - timedelta(days=n)) for n in (30, 90, 180)}
    visible = (as_of, as_of)
    users = [dict(r) for r in con.execute(
        'SELECT * FROM users WHERE first_seen_at<? AND recorded_at<=? ORDER BY user_id', visible)]
    periods = defaultdict(list)
    for r in con.execute('SELECT * FROM collection_periods WHERE recorded_at<=? ORDER BY starts_at,ends_at', (as_of,)):
        # Equivalent interval union; avoids scanning hundreds of daily confirmations per user.
        merged = periods[r['domain']]
        if merged and r['starts_at'] <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], r['ends_at']))
        else:
            merged.append((r['starts_at'], r['ends_at']))
    by_user = {}
    for table in ('profile_events','acquisition_events','membership_events','activity_events','participation_events'):
        rows = con.execute(f'''SELECT * FROM {table} WHERE occurred_at<? AND recorded_at<=?
                               ORDER BY occurred_at,recorded_at,event_id''', visible)
        by_user[table] = _group(rows, 'user_id')
    offers = _group(con.execute('''SELECT * FROM offers WHERE sent_at<? AND recorded_at<=?
                                   AND status='sent' ORDER BY sent_at,recorded_at,offer_id''', visible), 'user_id')
    orders = _group(con.execute('''SELECT * FROM orders WHERE ordered_at<? AND recorded_at<=?
                                   ORDER BY ordered_at,order_id''', visible), 'user_id')
    payments = _group(con.execute('''SELECT p.* FROM payments p JOIN orders o USING(order_id)
                     WHERE p.occurred_at<? AND p.recorded_at<=? AND o.ordered_at<? AND o.recorded_at<=?
                     ORDER BY p.occurred_at,p.payment_id''', (*visible, *visible)), 'order_id')
    items = _group(con.execute('''SELECT i.* FROM order_items i JOIN orders o USING(order_id)
                                  WHERE o.ordered_at<? AND o.recorded_at<=?''', visible), 'order_id')
    main_channels = [r[0] for r in con.execute('''SELECT channel_id FROM channels
                    WHERE kind='own_main' AND created_at<? AND recorded_at<=?''', visible)]
    # План может быть известен до старта курса, но его версия должна уже действовать.
    courses = {}
    for r in con.execute('''SELECT v.*,c.topic_id FROM course_versions v JOIN courses c USING(course_id)
                            WHERE v.effective_at<=? AND v.recorded_at<=? AND c.created_at<? AND c.recorded_at<=?
                            ORDER BY v.effective_at,v.recorded_at,v.version_id''', (*visible, *visible)):
        courses[r['course_id']] = dict(r)

    for user in users:
        uid, start = user['user_id'], user['first_seen_at']
        f = {name: None for name in FEATURE_NAMES}
        quality = {}

        def put(name, value, status='observed', **detail):
            f[name] = value
            quality[name] = {'status': status, **detail}

        def coverage(domain, window=None):
            left = max(start, cutoffs[window]) if window else start
            return _covered(periods, domain, left, as_of)

        def gated(name, value, domain, window=None, no_value='no_observed_event'):
            complete = coverage(domain, window)
            put(name, value if complete else None,
                ('complete' if value is not None else no_value) if complete else 'incomplete_coverage',
                observation_start=max(start,cutoffs[window]) if window else start, observation_end=as_of)

        put('days_since_first_seen', _days(as_of, start))
        put('calendar_month', _date(as_of).month)
        acquisitions = by_user['acquisition_events'][uid]
        if acquisitions:
            first = acquisitions[0]
            put('first_source_channel_id', first['source_channel_id'],
                'observed_route' if first['source_channel_id'] else 'unknown_source',
                source_event_id=first['event_id'], mechanism=first['mechanism'])
        else:
            put('first_source_channel_id', None, 'unknown_source')
        profile = {}
        for event in by_user['profile_events'][uid]:
            profile[event['field']] = event
        for field in ('education_stage','job_search_status'):
            if field in profile:
                event = profile[field]
                put(field, event['value'], 'declared', source=event['source'],
                    answer_age_days=_days(as_of, event['occurred_at']))
            else:
                put(field, None, 'not_answered')

        activity = by_user['activity_events'][uid]
        selected = [r for r in activity if r['event_type']=='topic_selected' and r['occurred_at']>=cutoffs[90]]
        topic = None
        if selected:
            counts = Counter(r['topic_id'] for r in selected)
            last = {r['topic_id']: r['occurred_at'] for r in selected}
            topic = max(counts, key=lambda k: (counts[k], last[k], k))
        gated('primary_topic_id', topic, 'activity', 90, 'no_topic_selection')

        states = {}
        for event in by_user['membership_events'][uid]:
            if event['channel_id'] in main_channels:
                states[event['channel_id']] = event
        confirmed = {}
        for channel, event in states.items():
            if _covered(periods, 'membership', event['occurred_at'], as_of):
                confirmed[channel] = int(event['status']=='joined')
        if any(confirmed.values()):
            put('is_main_channel_member', 1, 'complete')
        elif main_channels and len(confirmed)==len(main_channels):
            put('is_main_channel_member', 0, 'complete')
        else:
            put('is_main_channel_member', None, 'unknown_initial_state_or_gap')
        if activity:
            last_time = activity[-1]['occurred_at']
            if _covered(periods, 'activity', last_time, as_of):
                put('days_since_last_activity', _days(as_of, last_time), 'complete')
            else:
                put('days_since_last_activity', None, 'incomplete_coverage')
        else:
            put('days_since_last_activity', None, 'no_observed_event')
        activity30 = [r for r in activity if r['occurred_at']>=cutoffs[30]]
        gated('active_days_30d', len({r['occurred_at'][:10] for r in activity30}), 'activity', 30)
        intent_types = {'course_program_requested','price_requested','waitlist_joined','trial_started'}
        gated('course_intent_actions_30d', sum(r['event_type'] in intent_types for r in activity30), 'activity', 30)

        paid, owned_courses, net90 = [], set(), 0
        for order in orders[uid]:
            order_payments = payments[order['order_id']]
            # Частичные платежи суммируются, дата покупки = достижение всей суммы.
            # Возврат остаётся денежным событием и не удаляет историческую покупку.
            balance, paid_at = 0, None
            deltas = defaultdict(int)
            for payment in order_payments:
                delta = payment['amount_minor'] * (1 if payment['kind']=='payment' else -1)
                deltas[payment['occurred_at']] += delta
                if payment['occurred_at']>=cutoffs[90]:
                    net90 += delta
            for when,delta in sorted(deltas.items()):
                balance += delta
                if order['total_minor']>0 and balance>=order['total_minor'] and paid_at is None:
                    paid_at = when
            if paid_at:
                paid.append((paid_at, order['order_id'], balance))
                if balance>0:
                    owned_courses.update(r['course_id'] for r in items[order['order_id']])
        paid.sort()
        paid90 = [r for r in paid if r[0]>=cutoffs[90]]
        gated('orders_count_90d', len(paid90), 'commerce')
        gated('net_spend_90d_minor', net90, 'commerce', 90)
        gated('mean_order_value_90d_minor', sum(r[2] for r in paid90)/len(paid90) if paid90 else None, 'commerce',
              no_value='no_paid_orders')
        gated('days_since_last_purchase', _days(as_of, paid[-1][0]) if paid else None, 'commerce', no_value='no_paid_orders')
        gap = _days(paid[-1][0], paid[0][0])/(len(paid)-1) if len(paid)>1 else None
        gated('mean_purchase_gap_days', gap, 'commerce', no_value='fewer_than_two_paid_orders')
        gated('paid_courses_count', len(owned_courses), 'commerce')
        attended = {r['hackathon_id'] for r in by_user['participation_events'][uid]
                    if r['status'] in ('attended','solution_submitted') and r['occurred_at']>=cutoffs[180]}
        gated('hackathons_attended_180d', len(attended), 'participation', 180)

        sent = offers[uid]
        gated('offers_sent_30d', sum(r['sent_at']>=cutoffs[30] for r in sent), 'offers', 30)
        # Новейшее предложение курса заменяет предыдущие, даже если уже истекло.
        latest_by_course = {r['course_id']: r for r in sent}
        active = [r for r in latest_by_course.values() if r['valid_until']>as_of]
        if active:
            latest = max(active, key=lambda r: (r['sent_at'],r['recorded_at'],r['offer_id']))
            discount = 100 * (1-latest['offered_price_minor']/latest['regular_price_minor'])
        else:
            discount = None
        gated('active_offer_discount_pct', discount, 'offers', no_value='no_active_offer')

        next_dates = [c['starts_at'] for c in courses.values()
                      if f['primary_topic_id'] is not None and c['topic_id']==f['primary_topic_id']
                      and c['course_id'] not in owned_courses and not c['is_cancelled']
                      and c['starts_at']>=as_of and c['sales_close_at']>as_of]
        launch_gap = _days(min(next_dates), as_of) if next_dates else None
        if not coverage('commerce'):
            put('days_to_next_relevant_course_start', None, 'incomplete_coverage')
        elif f['primary_topic_id'] is None:
            put('days_to_next_relevant_course_start', None, 'no_reliable_topic')
        else:
            put('days_to_next_relevant_course_start', launch_gap,
                'known_catalog' if launch_gap is not None else 'no_known_relevant_launch')
        assert set(f)==set(FEATURE_NAMES) and set(quality)==set(FEATURE_NAMES)
        yield {'user_id':uid, 'as_of':as_of, 'feature_version':'v1', **f,
               'quality_json':json.dumps(quality, ensure_ascii=False, sort_keys=True), 'built_at':utc()}


def build_features(con, as_of):
    """Материализовать снимок. Не читает информацию, зарегистрированную после as_of.

    as_of -- правая исключённая граница событий. Регистрация и каталог могут быть
    известны ровно в as_of. Повтор пересчитывает только выбранную дату/версию.
    """
    as_of = utc(as_of)
    if as_of>utc():
        raise ValueError('Нельзя строить наблюдаемые признаки на будущую дату.')
    columns = ['user_id','as_of','feature_version',*FEATURE_NAMES,'quality_json','built_at']
    with con:
        con.execute('BEGIN IMMEDIATE')
        con.execute("DELETE FROM user_features WHERE as_of=? AND feature_version='v1'", (as_of,))
        n = 0
        for row in _compute(con, as_of):
            con.execute(f'INSERT INTO user_features ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                        [row[k] for k in columns])
            n += 1
    return n


def export_features(con, as_of, path):
    """CSV: 3 ключа + ровно 20 признаков; quality_json остаётся в БД."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ['user_id','as_of','feature_version',*FEATURE_NAMES]
    rows = con.execute(f'SELECT {",".join(columns)} FROM user_features WHERE as_of=? AND feature_version=? ORDER BY user_id',
                       (utc(as_of),'v1'))
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)
    return str(path)
