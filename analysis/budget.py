"""Budget and sample-size scenarios. Inputs labelled real, proposed or hypothetical."""
from pathlib import Path
from statistics import NormalDist
from math import ceil, sqrt
import argparse
import csv
import hashlib
import json

ROOT = Path(__file__).resolve().parent
Z = NormalDist()


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def sample_size(p0, p1, alpha=.05, power=.8):
    """Equal arms, two-sided normal planning approximation, no continuity correction."""
    if not 0 < p0 < p1 < 1 or not 0 < alpha < 1 or not .5 < power < 1:
        raise ValueError('Invalid planning parameters')
    q = (p0+p1)/2
    a = Z.inv_cdf(1-alpha/2)*sqrt(2*q*(1-q))
    b = Z.inv_cdf(power)*sqrt(p0*(1-p0)+p1*(1-p1))
    return ceil((a+b)**2/(p1-p0)**2)


def binary_mde(p0, per_arm):
    lo, hi = p0+1e-9, .999999
    for _ in range(70):
        mid = (lo+hi)/2
        if sample_size(p0, mid) > per_arm:
            lo = mid
        else:
            hi = mid
    return hi-p0


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--metrics',type=Path,default=ROOT.parent/'data/public/metrics.json')
    p.add_argument('--out',type=Path,default=Path('work/budget'))
    args=p.parse_args(); out=args.out
    out.mkdir(parents=True, exist_ok=True)
    source = json.loads(args.metrics.read_text(encoding='utf-8'))['sales']
    aov = source['mean_order_rub']
    stages = [
        {'stage': 'pilot', 'label': 'Измерительный пилот', 'cap_rub': 60000, 'share_pct': 20,
         'release_rule': 'Четыре кандидата; потолок 15000 на источник. Нужны фактическая цена и рабочая связь перехода с оплатой.',
         'result': 'Стоимость и качество наблюдаемых обращений; причинная окупаемость не заявляется.'},
        {'stage': 'confirmation', 'label': 'Подтверждение одной гипотезы', 'cap_rub': 90000, 'share_pct': 30,
         'release_rule': 'Есть контроль до рекламного контакта в нужной аудитории и достаточный заранее рассчитанный размер.',
         'result': 'Один отдельный эксперимент на новой выборке. Если дизайн недоступен, деньги остаются в резерве.'},
        {'stage': 'scale', 'label': 'Последовательное масштабирование', 'cap_rub': 150000, 'share_pct': 50,
         'release_rule': 'Нижняя граница дополнительного маржинального дохода покрывает дополнительные рекламные расходы.',
         'result': 'До трёх траншей по 50000 с новой проверкой условий цены и аудитории.'},
    ]
    assert sum(r['cap_rub'] for r in stages) == 300000
    power = []
    for p0 in (.01, .02, .05):
        for lift in (.2, .5):
            p1 = p0*(1+lift)
            n = sample_size(p0, p1)
            power.append({'evidence': 'hypothetical_scenario', 'p_control': p0, 'p_treatment': p1,
                          'absolute_lift_pp': (p1-p0)*100, 'relative_lift_pct': lift*100,
                          'alpha_two_sided': .05, 'power': .8, 'n_per_arm': n, 'n_total': 2*n,
                          'expected_control_buyers': p0*n})
    mde = [{'evidence': 'hypothetical_scenario', 'n_total': n, 'n_per_arm': n//2,
            'p_control': .02, 'mde_pp': binary_mde(.02, n//2)*100,
            'expected_control_buyers': .02*(n//2),
            'small_expected_count_warning': .02*(n//2) < 10}
           for n in (500, 1000, 2000, 5000, 10000)]
    economics = []
    for cost in (15000, 60000, 90000, 150000, 300000):
        for margin in (.4, .6, .8):
            economics.append({'evidence': 'historical_AOV_plus_hypothetical_margin', 'ad_cost_rub': cost,
                              'assumed_contribution_margin': margin, 'historical_order_value_rub': aov,
                              'incremental_revenue_break_even_rub': cost/margin,
                              'incremental_orders_break_even': ceil(cost/(margin*aov)),
                              'max_break_even_incremental_CAC_rub': margin*aov})
    cm_power = []
    for n in (1000, 2000, 5000, 10000):
        for cv in (0., .75, 1.5):
            # Hypothetical distribution: a zero for a nonbuyer, positive amount with given CV otherwise.
            p, margin, cost = .02, .6, 60000
            sigma = margin*aov*sqrt(p*(cv*cv+1-p))
            m = (Z.inv_cdf(.975)+Z.inv_cdf(.8))*sigma*sqrt(4/n)
            cm_power.append({'evidence': 'hypothetical_primary_metric_planning', 'n_total': n,
                             'p_control': p, 'positive_value_cv': cv, 'assumed_margin': margin,
                             'sigma_cm_per_eligible_rub': sigma, 'mde_cm_per_eligible_rub': m,
                             'incremental_ad_cost_rub': cost, 'cost_per_treatment_unit_rub': cost/(n/2),
                             'approx_effect_needed_to_detect_profit_rub_per_unit': cost/(n/2)+m})
    shortlist = [{'candidate_id': f'candidate_{i:02}', 'placement_id': f'future_p{i:02}',
                  'campaign_id': 'future_campaign', 'creative_id': f'future_c{i:02}',
                  'channel_url': '', 'quoted_cost_rub': '', 'pilot_cap_rub': 15000,
                  'publisher_audience_fit': '', 'placement_time': '', 'tracking_token': f'future_p{i:02}',
                  'known_overlap': '', 'decision': 'unfilled_candidate_not_a_booking'} for i in range(1,5)]
    for name, rows in [('budget_stages', stages), ('power_conversion', power), ('mde_by_audience', mde),
                       ('break_even', economics), ('power_contribution', cm_power), ('pilot_candidates', shortlist)]:
        write_csv(out/f'{name}.csv', rows)
    result = {'historical': {'orders': source['inferred_orders'], 'buyers': source['buyers'],
                            'revenue_rub': source['amount_rub'], 'mean_order_rub': aov,
                            'real_incremental_effect': None, 'real_incremental_ROMI': None,
                            'audience_size': None, 'conversion_base': None, 'contribution_margin': None},
              'proposal': {'budget_rub': 300000, 'stages': stages, 'actual_spend_rub': 0,
                           'not_an_estimate_of_market_prices': True, 'must_not_spend_reserve_without_release_conditions': True},
              'power_conversion': power, 'mde_by_audience': mde, 'break_even': economics,
              'power_contribution': cm_power,
              'primary_protocol': {'unit': 'eligible_person_before_contact', 'allocation': 'complete_randomization_1_to_1',
                  'primary_metric': '14_day_contribution_before_marketing_per_assigned_person',
                  'purchase_window_days': 14, 'reconciliation_lag_days': 7, 'primary_readout_day': 21,
                  'secondary_window_days': 28, 'secondary_readout_day': 35,
                  'primary_comparisons': 1, 'alpha_two_sided': .05,
                  'audience_and_contact_control_available': None,
                  'owned_message_effect_transfers_to_paid_acquisition': False},
              'historical_identification_status': 'no_randomized_assignment_no_control_no_eligible_nonbuyers',
              'input_sha256': {args.metrics.name: hashlib.sha256(args.metrics.read_bytes()).hexdigest()}}
    (out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'budget': result['proposal'], 'example_2_to_3_pct': power[3],
                      'historical_AOV_rub': aov}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
