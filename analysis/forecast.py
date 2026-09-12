"""Reproducible seven-day forecast. Analysis needs only Python's standard library."""
from pathlib import Path
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict, Counter
from statistics import mean, median, pstdev
import argparse
import csv
import hashlib
import json
import math

ROOT = Path(__file__).resolve().parent
H = 7
MIN_TRAIN = 14
MODELS = {
    'mean7': 'Среднее за 7 дней',
    'mean14': 'Среднее за 14 дней',
    'mean_all': 'Среднее всей истории',
    'seasonal7': 'Повтор прошлой недели',
    'weekday_shrunk': 'День недели со сглаживанием',
    'ewma03': 'Экспоненциальное сглаживание',
}
BASELINE = 'mean7'


def money(value):
    value = Decimal(''.join(value.split()).replace(',', '.')) * 100
    if value != value.to_integral_value() or value <= 0:
        raise ValueError('Expected a positive source amount with at most two decimals')
    return int(value)


def read_sales(path):
    orders, daily, courses = {}, {}, defaultdict(lambda: {'days': set(), 'lines': 0, 'cents': 0})
    people, rows = set(), []
    with path.open(encoding='utf-8-sig', newline='') as f:
        r = csv.DictReader(f)
        if r.fieldnames != ['Номер студента', 'Сумма', 'Курс', 'Время']:
            raise ValueError('Unexpected source columns')
        for row in r:
            if not all(row.values()):
                raise ValueError('Missing source value')
            t = datetime.strptime(row['Время'], '%d.%m.%Y %H:%M:%S')
            k = (row['Номер студента'], t)
            cents = money(row['Сумма'])
            orders[k] = orders.get(k, 0) + cents
            people.add(k[0])
            d = t.date().isoformat()
            daily.setdefault(d, {'date': d, 'orders': 0, 'amount_cents': 0, 'items': 0})
            daily[d]['items'] += 1
            c = courses[row['Курс']]
            c['days'].add(d)
            c['lines'] += 1
            c['cents'] += cents
            rows.append(row)
    for (_, t), cents in orders.items():
        z = daily[t.date().isoformat()]
        z['orders'] += 1
        z['amount_cents'] += cents
    d = [daily[k] for k in sorted(daily)]
    if len(d) < 2 * H + MIN_TRAIN:
        raise ValueError('Need at least 28 observed, complete calendar days')
    first = datetime.fromisoformat(d[0]['date'])
    for i, z in enumerate(d):
        if z['date'] != (first + timedelta(days=i)).date().isoformat():
            raise ValueError('A missing day is not automatically a zero-sales day')
        z['revenue_rub'] = z['amount_cents'] / 100
        z['mean_order_rub'] = z['revenue_rub'] / z['orders']
    segments = [{'course': k, 'active_days': len(v['days']), 'days_without_sales': len(d)-len(v['days']),
                 'items': v['lines'], 'amount_cents': v['cents'], 'revenue_rub': v['cents']/100}
                for k, v in sorted(courses.items())]
    assert sum(x['amount_cents'] for x in d) == sum(orders.values())
    return d, segments, {'rows': len(rows), 'orders': len(orders), 'buyers': len(people),
                         'revenue_cents': sum(orders.values()), 'days': len(d), 'courses': len(courses)}


def predict(y, model, horizon=H):
    """Only the supplied training prefix is visible. No future outcomes or post calendar."""
    y = list(y)
    if len(y) < MIN_TRAIN or horizon < 1 or model not in MODELS:
        raise ValueError('Invalid model, horizon or training length')
    if model.startswith('mean'):
        z = y if model == 'mean_all' else y[-int(model[4:]):]
        return [mean(z)] * horizon
    if model == 'seasonal7':
        return [y[-7 + i % 7] for i in range(horizon)]
    if model == 'weekday_shrunk':
        # A fixed 50/50 mixture; modulo positions are weekdays in a complete daily series.
        return [.5 * mean(y) + .5 * mean(y[(len(y) + i) % 7::7]) for i in range(horizon)]
    level = mean(y[:7])
    for z in y[7:]:
        level = .3 * z + .7 * level
    return [level] * horizon


def evaluate(y, dates, origins, metric, split):
    days, folds, scores = [], [], []
    for model in MODELS:
        my_folds = []
        for o in origins:
            actual = y[o:o+H]
            if len(actual) != H:
                raise ValueError('Incomplete test horizon')
            pred = predict(y[:o], model)
            f = {'metric': metric, 'split': split, 'model': model, 'train_days': o,
                 'origin': dates[o], 'train_end': dates[o-1], 'test_end': dates[o+H-1],
                 'actual_total': sum(actual), 'forecast_total': sum(pred),
                 'residual_total': sum(actual)-sum(pred),
                 'absolute_total_error': abs(sum(actual)-sum(pred)),
                 'daily_absolute_error_sum': sum(abs(a-p) for a, p in zip(actual, pred)),
                 'daily_squared_error_sum': sum((a-p)**2 for a, p in zip(actual, pred)),
                 'total_ape_pct': abs(sum(actual)-sum(pred))/sum(actual)*100 if sum(actual) else None}
            my_folds.append(f)
            for j, (a, p) in enumerate(zip(actual, pred), 1):
                days.append({'metric': metric, 'split': split, 'model': model,
                             'origin': dates[o], 'train_end': dates[o-1], 'date': dates[o+j-1],
                             'lead_day': j, 'actual': a, 'forecast': p, 'residual': a-p})
        folds.extend(my_folds)
        n = len(my_folds)
        scores.append({'metric': metric, 'split': split, 'model': model, 'label': MODELS[model],
                       'folds': n, 'forecast_day_pairs': n*H,
                       'weekly_mae': mean(f['absolute_total_error'] for f in my_folds),
                       'weekly_wape_pct': sum(f['absolute_total_error'] for f in my_folds)/sum(f['actual_total'] for f in my_folds)*100,
                       'daily_mae': sum(f['daily_absolute_error_sum'] for f in my_folds)/(n*H),
                       'daily_wape_pct': sum(f['daily_absolute_error_sum'] for f in my_folds)/sum(f['actual_total'] for f in my_folds)*100,
                       'daily_rmse': math.sqrt(sum(f['daily_squared_error_sum'] for f in my_folds)/(n*H)),
                       'bias_pct': -sum(f['residual_total'] for f in my_folds)/sum(f['actual_total'] for f in my_folds)*100})
    return days, folds, scores


def select_model(y, dates):
    """The last seven observed days are excluded from every selection fold."""
    end = len(y)-H
    origins = list(range(MIN_TRAIN, end-H+1))
    if not origins:
        raise ValueError('No development folds')
    d, f, s = evaluate(y[:end], dates[:end], origins, 'revenue_rub', 'development')
    # Stable registry order breaks a tie; do not optimize daily metrics after selection.
    selected = min(s, key=lambda x: x['weekly_mae'])['model']
    return selected, origins, d, f, s


def quantile(x, p):
    z = sorted(x)
    k = (len(z)-1)*p
    i = int(k)
    return z[i] + (z[min(i+1, len(z)-1)]-z[i])*(k-i)


def bounds(point, residuals):
    """Descriptive empirical range, not a calibrated prediction interval."""
    radius = quantile([abs(x) for x in residuals], .8)
    worst = max(abs(x) for x in residuals)
    return {'point': point, 'empirical_lower': max(0., point-radius),
            'empirical_upper': point+radius, 'absolute_error_p80': radius,
            'stress_lower': max(0., point-worst), 'stress_upper': point+worst,
            'signed_error_p10': quantile(residuals, .1),
            'signed_error_p90': quantile(residuals, .9), 'residual_windows': len(residuals)}


def write_csv(path, rows):
    if not rows:
        raise ValueError(f'Empty output: {path}')
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def run(source, out):
    daily, segments, control = read_sales(source)
    dates = [r['date'] for r in daily]
    y = [r['revenue_rub'] for r in daily]
    counts = [r['orders'] for r in daily]
    n = len(y)
    model, origins, dd, df, ds = select_model(y, dates)
    hd, hf, hs = evaluate(y, dates, [n-H], 'revenue_rub', 'holdout')
    # Orders are a secondary target, with a separate winner chosen only on development data.
    od, of, os = evaluate(counts[:n-H], dates[:n-H], origins, 'orders', 'development')
    omodel = min(os, key=lambda r: r['weekly_mae'])['model']
    ohd, ohf, ohs = evaluate(counts, dates, [n-H], 'orders', 'holdout')
    # After model selection and its final check, all past completed windows can describe uncertainty.
    cd, cf, _ = evaluate(y, dates, range(MIN_TRAIN, n-H+1), 'revenue_rub', 'range_history')
    ocd, ocf, _ = evaluate(counts, dates, range(MIN_TRAIN, n-H+1), 'orders', 'range_history')
    cutoff = datetime.fromisoformat(dates[-1]) + timedelta(days=1)
    future_dates = [(cutoff+timedelta(days=i)).date().isoformat() for i in range(H)]
    future, totals = [], []
    for metric, series, chosen, hist_d, hist_f in [('revenue_rub', y, model, cd, cf), ('orders', counts, omodel, ocd, ocf)]:
        pred = predict(series, chosen)
        residuals = [r['residual_total'] for r in hist_f if r['model'] == chosen]
        totals.append({'metric': metric, 'model': chosen, 'start': future_dates[0], 'end': future_dates[-1],
                       **bounds(sum(pred), residuals)})
        for j, p in enumerate(pred, 1):
            residuals = [r['residual'] for r in hist_d if r['model'] == chosen and r['lead_day'] == j]
            future.append({'metric': metric, 'date': future_dates[j-1], 'lead_day': j,
                           'model': chosen, **bounds(p, residuals)})
    all_forecasts = [{'metric': metric, 'model': m, 'label': MODELS[m],
                      'weekly_forecast': sum(predict(series, m))}
                     for metric, series in [('revenue_rub', y), ('orders', counts)] for m in MODELS]
    # Honest diagnostic: ranges estimated before holdout, checked on that final week.
    interval_checks = []
    for metric, chosen, dev_d, dev_f, hold_d, hold_f in [
        ('revenue_rub', model, dd, df, hd, hf), ('orders', omodel, od, of, ohd, ohf)]:
        for r in hold_d:
            if r['model'] != chosen:
                continue
            b = bounds(r['forecast'], [x['residual'] for x in dev_d if x['model']==chosen and x['lead_day']==r['lead_day']])
            interval_checks.append({'metric': metric, 'scope': 'day', 'date': r['date'], 'actual': r['actual'], **b,
                                    'inside_empirical_range': b['empirical_lower'] <= r['actual'] <= b['empirical_upper']})
        r = next(x for x in hold_f if x['model'] == chosen)
        b = bounds(r['forecast_total'], [x['residual_total'] for x in dev_f if x['model']==chosen])
        interval_checks.append({'metric': metric, 'scope': 'week', 'date': r['origin'], 'actual': r['actual_total'], **b,
                                'inside_empirical_range': b['empirical_lower'] <= r['actual_total'] <= b['empirical_upper']})
    wins = Counter()
    for o in origins:
        z = [f for f in df if f['train_days']==o]
        wins[min(z, key=lambda x: x['absolute_total_error'])['model']] += 1
    horizon_scores = []
    for split, rows in [('development', dd), ('holdout', hd)]:
        for m in MODELS:
            for h in (1, 3, 7):
                z = [r for r in rows if r['model']==m and r['lead_day']<=h]
                grouped = defaultdict(list)
                for r in z:
                    grouped[r['origin']].append(r)
                errors = [abs(sum(r['residual'] for r in g)) for g in grouped.values()]
                horizon_scores.append({'split': split, 'model': m, 'horizon': h, 'folds': len(grouped),
                                       'total_mae_rub': mean(errors),
                                       'total_wape_pct': sum(errors)/sum(r['actual'] for r in z)*100})
    period = {'start': dates[0], 'end': dates[-1], 'forecast_cutoff': cutoff.isoformat(),
              'forecast_start': future_dates[0], 'forecast_end': future_dates[-1], 'horizon_days': H,
              'timezone': 'sales_source_local_unspecified', 'all_observed_days_complete': True}
    protocol = {'min_train_days': MIN_TRAIN, 'development_origins': [dates[o] for o in origins],
                'development_end': dates[n-H-1], 'development_windows': len(origins),
                'development_unique_target_dates': len(set(r['date'] for r in dd)),
                'holdout_start': dates[n-H], 'holdout_end': dates[-1],
                'selection_metric': 'mean_absolute_error_of_7_day_total',
                'baseline': BASELINE, 'selected_revenue_model': model, 'selected_orders_model': omodel,
                'overlapping_windows': True, 'range_windows': n-H-MIN_TRAIN+1,
                'holdout_used_for_selection': False, 'range_is_calibrated': False}
    active = [r['active_days'] for r in segments]
    top = sorted(daily, key=lambda r: r['revenue_rub'], reverse=True)[:5]
    summary = {'input': {'name': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()},
               'control': control, 'period': period, 'protocol': protocol,
               'models': MODELS, 'development_scores': ds, 'holdout_scores': hs,
               'orders_development_scores': os, 'orders_holdout_scores': ohs,
               'future_week': totals, 'future_all_models': all_forecasts,
               'development_fold_wins': {m: wins[m] for m in MODELS},
               'holdout_range_check': interval_checks,
               'variability': {'daily_mean_rub': mean(y), 'daily_median_rub': median(y),
                               'daily_sd_rub': pstdev(y), 'daily_cv': pstdev(y)/mean(y),
                               'top5_revenue_share_pct': sum(r['revenue_rub'] for r in top)/sum(y)*100,
                               'top5_dates': [r['date'] for r in top],
                               'course_active_days_min': min(active), 'course_active_days_median': median(active),
                               'course_active_days_max': max(active),
                               'weekly_observed_min_rub': min(sum(y[i:i+H]) for i in range(n-H+1)),
                               'weekly_observed_max_rub': max(sum(y[i:i+H]) for i in range(n-H+1))},
               'limitations': [
                   'All timestamps retain the unspecified local sales timezone.',
                   'A seven-day projection from the data cutoff, not a claim of known sales on September 11-12.',
                   '38 days and overlapping validation windows do not establish stable accuracy or calibrated coverage.',
                   'A separate last week is excluded from algorithmic model selection; the full dataset was available in earlier EDA.',
                   'Marketing calendar is descriptive context only: complete advance plans and an as-of revision log are missing.',
                   'No product- or channel-level forecast, no causal uplift, no budget-dependent revenue claim.',
                   'Orders are reconstructed timestamp groups, not verified payment IDs; revenue is the agreed source-price sum.',
                   'Empirical weekly bounds use weekly residuals directly, not a sum of daily bounds.',
                   'Empirical ranges reuse development data, are dependent across windows, and have no guaranteed probability.',
               ]}
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in [('daily_sales', daily), ('course_sparsity', segments),
                       ('backtest_daily', dd+hd+od+ohd), ('backtest_windows', df+hf+of+ohf),
                       ('model_scores', ds+hs+os+ohs), ('forecast_daily', future), ('forecast_week', totals),
                       ('forecast_all_models', all_forecasts), ('historical_range_errors', cf+ocf),
                       ('holdout_range_check', interval_checks), ('horizon_sensitivity', horizon_scores)]:
        write_csv(out / f'{name}.csv', rows)
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--out', type=Path, default=Path('work/forecast'))
    a = ap.parse_args()
    s = run(a.input, a.out)
    print(json.dumps({'control': s['control'], 'protocol': s['protocol'], 'forecast': s['future_week']}, ensure_ascii=False, indent=2))
