"""Local individual holdout calculator. No delivery, tracking or real experiment is performed."""
from pathlib import Path
from datetime import datetime, timedelta
from statistics import mean, variance
import argparse
import csv
import hashlib
import json
import math
import random
from scipy.stats import t

ROOT = Path(__file__).resolve().parent


def timestamp(x):
    v = datetime.fromisoformat(x.replace('Z', '+00:00'))
    if v.tzinfo is None:
        raise ValueError('Experiment timestamps require an explicit timezone')
    return v


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def assign(ids, config, seed):
    if len(ids) != len(set(ids)) or len(ids) < 4 or any(not s for s in ids):
        raise ValueError('A complete roster of unique eligible people is required')
    order = sorted(ids)
    random.Random(seed).shuffle(order)
    treatment = set(order[:len(order)//2])
    return [{'dataset_id': config['dataset_id'], 'experiment_id': config['experiment_id'],
             'user_key': u, 'arm': 'T' if u in treatment else 'C', 'assigned_at': config['assigned_at']}
            for u in sorted(ids)]


def difference(treatment, control, alpha=.05):
    """Welch interval at the randomized person level; approximate for sparse/skewed outcomes."""
    nt, nc = len(treatment), len(control)
    if min(nt, nc) < 2:
        raise ValueError('At least two randomized people per arm are required')
    a, b = variance(treatment)/nt, variance(control)/nc
    delta = mean(treatment)-mean(control)
    if a+b == 0:
        return {'mean_t': mean(treatment), 'mean_c': mean(control), 'difference': delta,
                'se': 0., 'ci_low': None, 'ci_high': None, 'df': None, 'status': 'zero_variance_not_a_precision_claim'}
    df = (a+b)**2/(a*a/(nt-1)+b*b/(nc-1))
    se = math.sqrt(a+b)
    radius = float(t.ppf(1-alpha/2, df))*se
    return {'mean_t': mean(treatment), 'mean_c': mean(control), 'difference': delta, 'se': se,
            'ci_low': delta-radius, 'ci_high': delta+radius, 'df': df, 'status': 'approximate_welch_interval'}


def analyze(assignments, outcomes, config, as_of):
    if config['randomization_unit'] != 'person' or config['allocation'] != 'complete_1_to_1':
        raise ValueError('This calculator does not analyze clustered or unmatched experiments')
    if config.get('assignment_before_contact_confirmed') is not True:
        raise ValueError('Random assignment before contact is not confirmed')
    start = timestamp(config['assigned_at'])
    end = start+timedelta(days=config['window_days'])
    ready = end+timedelta(days=config['reconciliation_lag_days'])
    if timestamp(as_of) < ready:
        raise ValueError('Outcome window or reconciliation period is not mature')
    if len(assignments) != config['planned_n_total']:
        raise ValueError('Assignment roster differs from the frozen planned cohort')
    expected = {r['user_key']: r for r in assignments}
    if len(expected) != len(assignments):
        raise ValueError('Duplicate assignment')
    observed = {r['user_key']: r for r in outcomes}
    if len(observed) != len(outcomes) or set(observed) != set(expected):
        raise ValueError('Every assigned person needs one explicit complete outcome, including zeros')
    groups = {'T': [], 'C': []}
    known_costs = True
    for u, a in expected.items():
        r = observed[u]
        if a['arm'] not in groups:
            raise ValueError('Unexpected arm')
        if a['dataset_id'] != config['dataset_id'] or r['dataset_id'] != config['dataset_id']:
            raise ValueError('Mixed datasets')
        if a['experiment_id'] != config['experiment_id'] or r['experiment_id'] != config['experiment_id']:
            raise ValueError('Mixed experiments')
        if timestamp(a['assigned_at']) != start:
            raise ValueError('Staggered enrollment is not supported by this cohort calculator')
        if timestamp(r['window_start']) != start or timestamp(r['window_end']) != end:
            raise ValueError('Wrong outcome window')
        if r['complete'] != '1' or timestamp(r['reconciled_through']) < ready or timestamp(r['reconciled_through']) > timestamp(as_of):
            raise ValueError('Incomplete or incorrectly dated outcome reconciliation')
        for k in ['gross_paid_cents', 'refund_cents', 'paid_buyer']:
            if r[k] == '' or int(r[k]) < 0:
                raise ValueError('Missing/nonnegative amount required')
        gross, refunds = int(r['gross_paid_cents']), int(r['refund_cents'])
        paid = int(r['paid_buyer'])
        if paid not in (0, 1) or paid != int(gross > 0) or refunds > gross:
            raise ValueError('Invalid buyer or refund aggregate')
        variable = None if r['variable_cost_cents'] == '' else int(r['variable_cost_cents'])
        if variable is not None and variable < 0:
            raise ValueError('Variable costs must be nonnegative; refunds belong in their own field')
        known_costs &= variable is not None
        net = (gross-refunds)/100
        groups[a['arm']].append({'net_revenue': net, 'paid_buyer': paid,
                                 'contribution': None if variable is None else net-variable/100})
    nt, nc = len(groups['T']), len(groups['C'])
    if (nt, nc) != (len(assignments)//2, len(assignments)-len(assignments)//2):
        raise ValueError('Arm counts violate the frozen complete 1:1 assignment')
    result = {'dataset_id': config['dataset_id'], 'experiment_id': config['experiment_id'],
              'evidence': config['evidence'], 'scope': config['intervention_scope'],
              'n_t': nt, 'n_c': nc, 'window_days': config['window_days'], 'as_of': as_of,
              'intent_to_treat': True, 'nonbuyers_in_denominator': True,
              'approximate_intervals': True, 'metrics': {}, 'economic_decision': {}}
    for field in ['paid_buyer', 'net_revenue']+(['contribution'] if known_costs else []):
        result['metrics'][field] = difference([r[field] for r in groups['T']], [r[field] for r in groups['C']])
    cost_t, cost_c = config.get('marketing_cost_t_cents'), config.get('marketing_cost_c_cents')
    if not known_costs or cost_t is None or cost_c is None:
        result['economic_decision'] = {'status': 'costs_unknown', 'incremental_profit_rub': None,
                                       'ROMI_incremental_contribution': None, 'release_budget': False}
        return result
    if cost_t < 0 or cost_c < 0:
        raise ValueError('Marketing costs cannot be negative')
    delta_cost = (cost_t-nt/nc*cost_c)/100
    r = result['metrics']['contribution']
    profit = nt*r['difference']-delta_cost
    lower = None if r['ci_low'] is None else nt*r['ci_low']-delta_cost
    upper = None if r['ci_high'] is None else nt*r['ci_high']-delta_cost
    econ = {'incremental_marketing_cost_rub': delta_cost,
            'incremental_contribution_rub': nt*r['difference'],
            'incremental_profit_rub': profit, 'profit_ci_low': lower, 'profit_ci_high': upper,
            'ROMI_incremental_contribution': profit/delta_cost if delta_cost>0 else None,
            'paid_acquisition_transfer_supported': config['intervention_scope']=='paid_delivery_holdout',
            'status': 'interval_unavailable' if lower is None else 'positive_lower_bound' if lower>0 else 'negative_upper_bound' if upper<0 else 'inconclusive',
            'release_budget': False}
    # Synthetic output can never authorize a real budget; a valid interval still requires business review.
    econ['review_for_same_scope_scale'] = config['evidence']=='real_experiment' and lower is not None and lower>0
    result['economic_decision'] = econ
    result['warnings'] = ['Approximate intervals can be unreliable with few buyers or heavy-tailed amounts.',
                          'Exposure/clicks are not used to remove assigned people.',
                          'No generalization to another audience, price, course or acquisition channel is automatic.']
    return result


def demo(out):
    config = {'dataset_id': 'synthetic_holdout', 'experiment_id': 'demo_offer_14d', 'evidence': 'synthetic',
              'randomization_unit': 'person', 'allocation': 'complete_1_to_1', 'planned_n_total': 2000,
              'assigned_at': '2026-08-01T00:00:00+00:00', 'window_days': 14, 'reconciliation_lag_days': 7,
              'assignment_before_contact_confirmed': True, 'intervention_scope': 'owned_message',
              'marketing_cost_t_cents': 6000000, 'marketing_cost_c_cents': 0}
    ids = [f'synthetic_{i:05}' for i in range(config['planned_n_total'])]
    a = assign(ids, config, 1707)
    by_arm = {arm: [r['user_key'] for r in a if r['arm']==arm] for arm in ('T','C')}
    buyers = set(by_arm['T'][:30]+by_arm['C'][:20])
    o = [{'dataset_id': config['dataset_id'], 'experiment_id': config['experiment_id'], 'user_key': u,
          'window_start': config['assigned_at'], 'window_end': '2026-08-15T00:00:00+00:00',
          'reconciled_through': '2026-08-22T00:00:00+00:00', 'complete': '1',
          'gross_paid_cents': '1000000' if u in buyers else '0', 'refund_cents': '0',
          'variable_cost_cents': '400000' if u in buyers else '0', 'paid_buyer': '1' if u in buyers else '0'} for u in ids]
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out/'assignments.csv', a)
    write_csv(out/'outcomes.csv', o)
    (out/'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    result = analyze(a, o, config, '2026-08-22T00:00:00+00:00')
    result['illustrative_attribution_comparison'] = {'assumption': 'If all treatment-arm revenue were attributed to the message',
        'attributed_revenue_rub': 300000, 'marketing_cost_rub': 60000, 'ROMI_attributed_revenue': 4.,
        'incremental_revenue_rub': 100000, 'ROMI_incremental_revenue': 2/3,
        'incremental_contribution_rub': 60000, 'incremental_profit_rub': 0, 'ROMI_incremental_contribution': 0.}
    (out/'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return result


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('demo')
    d.add_argument('--out', type=Path, default=ROOT/'results'/'synthetic_demo')
    a = sub.add_parser('analyze')
    for key in ('assignments', 'outcomes', 'config', 'out'):
        a.add_argument('--'+key, type=Path, required=True)
    a.add_argument('--as-of', required=True)
    r = sub.add_parser('assign')
    r.add_argument('--roster', type=Path, required=True)
    r.add_argument('--config', type=Path, required=True)
    r.add_argument('--seed', type=int, required=True)
    r.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    if args.cmd == 'demo':
        result = demo(args.out)
    elif args.cmd == 'analyze':
        result = analyze(read_csv(args.assignments), read_csv(args.outcomes), json.loads(args.config.read_text()), args.as_of)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    else:
        if args.out.exists():
            raise ValueError('Do not overwrite a frozen assignment file')
        c = json.loads(args.config.read_text())
        result = assign([r['user_key'] for r in read_csv(args.roster)], c, args.seed)
        write_csv(args.out, result)
        result = {'assigned': len(result), 'sha256': hashlib.sha256(args.out.read_bytes()).hexdigest()}
    print(json.dumps(result.get('economic_decision', result), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
