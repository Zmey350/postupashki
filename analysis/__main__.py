"""Aggregate the owner-supplied sales CSV; never export customer-level records."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from statistics import median
import argparse
import csv
import hashlib
import json

FIELDS = ['Номер студента', 'Сумма', 'Курс', 'Время']


def money(value):
    value = Decimal(''.join(value.split()).replace(',', '.')) * 100
    if not value.is_finite() or value != value.to_integral_value() or value <= 0:
        raise ValueError('Expected a positive amount with at most two decimal places')
    return int(value)


def read_sales(path):
    rows = []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != FIELDS:
            raise ValueError(f'Expected columns: {FIELDS}')
        for line, raw in enumerate(reader, 2):
            if None in raw or any(not raw.get(k, '').strip() for k in FIELDS):
                raise ValueError(f'Missing or extra field at CSV line {line}')
            rows.append({'user': raw['Номер студента'].strip(), 'course': raw['Курс'].strip(),
                         'time': datetime.strptime(raw['Время'], '%d.%m.%Y %H:%M:%S'),
                         'cents': money(raw['Сумма']), 'raw': tuple(raw[k] for k in FIELDS)})
    if not rows:
        raise ValueError('Empty sales input')
    return rows


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def aggregate(source, out):
    source, out = Path(source), Path(out)
    rows = read_sales(source)
    groups = defaultdict(list)
    for row in rows:
        groups[row['user'], row['time']].append(row)
    orders = [{'user': u, 'time': t, 'cents': sum(r['cents'] for r in rs),
               'courses': tuple(sorted({r['course'] for r in rs})), 'items': len(rs)}
              for (u, t), rs in sorted(groups.items())]
    by_user = defaultdict(list)
    for order in orders:
        by_user[order['user']].append(order)
    bundles = [o for o in orders if len(o['courses']) > 1]
    total = sum(r['cents'] for r in rows)
    repeats = [o for os in by_user.values() for o in os[1:]]
    start, end = min(r['time'] for r in rows).date(), max(r['time'] for r in rows).date()
    daily = []
    for i in range((end - start).days + 1):
        day = start + timedelta(days=i)
        os = [o for o in orders if o['time'].date() == day]
        daily.append({'date': day.isoformat(), 'observed': bool(os),
                      'orders': len(os) if os else None,
                      'buyers': len({o['user'] for o in os}) if os else None,
                      'items': sum(o['items'] for o in os) if os else None,
                      'amount_cents': sum(o['cents'] for o in os) if os else None,
                      'amount_rub': sum(o['cents'] for o in os)/100 if os else None})
    products = []
    for course in sorted({r['course'] for r in rows}):
        rs = [r for r in rows if r['course'] == course]
        products.append({'course': course, 'items': len(rs), 'buyers': len({r['user'] for r in rs}),
                         'amount_cents': sum(r['cents'] for r in rs),
                         'amount_rub': sum(r['cents'] for r in rs)/100})
    products.sort(key=lambda r: -r['amount_cents'])
    weekly = []
    for monday in sorted({o['time'].date()-timedelta(days=o['time'].weekday()) for o in orders}):
        sunday = monday + timedelta(days=6)
        os = [o for o in orders if monday <= o['time'].date() <= sunday]
        ds = [d for d in daily if monday.isoformat() <= d['date'] <= sunday.isoformat()]
        weekly.append({'week_start': monday.isoformat(), 'week_end': sunday.isoformat(),
                       'observed_days': sum(d['observed'] for d in ds),
                       'full_calendar_week_in_export': monday >= start and sunday <= end,
                       'orders': len(os), 'buyers': len({o['user'] for o in os}),
                       'amount_cents': sum(o['cents'] for o in os),
                       'amount_rub': sum(o['cents'] for o in os)/100})
    patterns = Counter(o['courses'] for o in bundles)
    packages = [{'courses': ' + '.join(cs), 'orders': n,
                 'amount_cents': sum(o['cents'] for o in bundles if o['courses'] == cs)}
                for cs, n in patterns.most_common()]
    pairs = Counter(pair for o in bundles for pair in combinations(o['courses'], 2))
    pair_table = [{'course_a': a, 'course_b': b, 'orders': n} for (a, b), n in pairs.most_common()]
    quality = {'missing_values': 0, 'exact_duplicates': len(rows)-len({r['raw'] for r in rows}),
               'nonpositive_amounts': 0,
               'duplicate_course_inside_order': sum(o['items'] != len(o['courses']) for o in orders),
               'unobserved_calendar_days': sum(not d['observed'] for d in daily)}
    metrics = {
        'schema_version': 1, 'dataset_id': 'legacy_sales_2026', 'provenance': 'real',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'period': {'start': start.isoformat(), 'end': end.isoformat(),
                   'calendar_days': len(daily), 'dates_with_sales': sum(d['observed'] for d in daily),
                   'boundary_days_assumed_complete': True, 'timezone': 'unspecified_local'},
        'sales': {'rows': len(rows), 'buyers': len(by_user), 'courses': len(products),
                  'inferred_orders': len(orders), 'amount_cents': total, 'amount_rub': total/100,
                  'mean_order_rub': total/100/len(orders), 'median_order_rub': median(o['cents'] for o in orders)/100,
                  'bundle_orders': len(bundles), 'bundle_order_share_pct': 100*len(bundles)/len(orders),
                  'bundle_amount_rub': sum(o['cents'] for o in bundles)/100,
                  'bundle_sales_share_pct': 100*sum(o['cents'] for o in bundles)/total,
                  'repeat_buyers': sum(len(os)>1 for os in by_user.values()),
                  'repeat_buyer_share_pct': 100*sum(len(os)>1 for os in by_user.values())/len(by_user),
                  'repeat_orders': len(repeats), 'repeat_amount_rub': sum(o['cents'] for o in repeats)/100,
                  'top5_day_sales_share_pct': 100*sum(sorted((d['amount_cents'] or 0 for d in daily),reverse=True)[:5])/total,
                  'top4_product_sales_share_pct': 100*sum(p['amount_cents'] for p in products[:4])/total},
        'quality': quality,
        'attribution': {'observed_individual_touches': 0, 'known_ad_costs': 0,
                        'coverage_pct': 0.0, 'unknown_source_rub': total/100, 'historical_romi_pct': None,
                        'basis': 'CSV contains no advertising-source, contact or cost fields'},
        'knowledge': {
            'what_we_know': 'Наблюдаем строки продаж, суммы, курсы и время в предоставленной выгрузке.',
            'what_we_estimate': 'Восстанавливаем заказы и пакеты по совпадению покупателя и секунды; прогнозируем будущие продажи.',
            'what_we_cannot_know_yet': 'Источники покупок, конверсия всей аудитории, истинный ROMI и причинный эффект рекламы.'},
    }
    if sum(o['cents'] for o in orders) != total:
        raise AssertionError('Order grouping changed the source money total')
    out.mkdir(parents=True, exist_ok=True)
    for name, table in [('daily',daily),('weekly',weekly),('products',products),('packages',packages),('product_pairs',pair_table)]:
        write_csv(out/f'{name}.csv', table)
    (out/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='Owner-supplied CSV export of base.xlsx')
    parser.add_argument('--out', type=Path, default=Path('work/real_analysis'))
    args = parser.parse_args()
    result = aggregate(args.input, args.out)
    print(json.dumps(result['sales'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
