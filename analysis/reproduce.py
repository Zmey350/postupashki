"""Reproduce safe aggregates, real-sales backtest and attribution comparisons."""
from pathlib import Path
import argparse
import json
import subprocess
import sys
from .__main__ import aggregate
from . import forecast


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path('work/reproduced_analysis'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    source, out = args.input.resolve(), args.out.resolve()
    m = aggregate(source, out)
    f = forecast.run(source, out/'forecast')
    for script, folder in [('marketing/analyze_history.py','marketing'),
                           ('attribution/compare_attribution.py','attribution')]:
        subprocess.run([sys.executable,str(root/script),'--input',str(source),'--out',str(out/folder)],
                       check=True,stdout=subprocess.DEVNULL)
    h = json.loads((out/'marketing/summary.json').read_text(encoding='utf-8'))
    a = json.loads((out/'attribution/summary.json').read_text(encoding='utf-8'))
    m['forecast'] = {
        'status': 'Реализовано и проверено',
        'scope': 'Ретроспективный расчёт на реальных продажах; качество будущего прогноза не подтверждено',
        'models_compared': len(f['models']), 'selected_model': f['protocol']['selected_revenue_model'],
        'development_windows': f['protocol']['development_windows'],
        'development_unique_target_dates': f['protocol']['development_unique_target_dates'],
        'overlapping_windows': True, 'holdout_used_for_model_selection': False,
        'holdout_start': f['protocol']['holdout_start'], 'holdout_end': f['protocol']['holdout_end'],
        'development_weekly_mae_rub': next(r['weekly_mae'] for r in f['development_scores'] if r['model']=='weekday_shrunk'),
        'baseline_development_weekly_mae_rub': next(r['weekly_mae'] for r in f['development_scores'] if r['model']=='mean7'),
        'holdout_weekly_wape_pct': next(r['weekly_wape_pct'] for r in f['holdout_scores'] if r['model']=='weekday_shrunk'),
        'baseline_holdout_weekly_wape_pct': next(r['weekly_wape_pct'] for r in f['holdout_scores'] if r['model']=='mean7'),
        'forecast_start': f['period']['forecast_start'], 'forecast_end': f['period']['forecast_end'],
        'revenue_point_rub': f['future_week'][0]['point'],
        'revenue_empirical_lower_rub': f['future_week'][0]['empirical_lower'],
        'revenue_empirical_upper_rub': f['future_week'][0]['empirical_upper'],
        'orders_point': f['future_week'][1]['point'], 'range_is_calibrated': False,
        'holdout_range_covers_week': False,
    }
    m['marketing_history'] = {
        'status': 'Реализовано частично',
        'scope': 'Импорт и сверка реального HTML проверены; история неполна, медиа не прочитаны',
        'source_messages': h['export_source_message_count'],
        'selected_window_start': '2026-07-28', 'selected_window_end': '2026-09-11',
        'selected_messages': h['export_period_messages'],
        'text_messages': h['export_period_text_messages'], 'media_only_messages': h['export_period_media_only_messages'],
        'messages_during_sales_period': h['export_sales_period_messages'],
        'direct_course_promotion_messages': h['direct_course_promotion_messages'],
        'missing_known_posts': h['known_main_channel_posts_missing_from_export'],
        'periods': h['periods'],
        'promo_price_and_product_matching_orders': h['extended_sale_window']['strict_orders'],
        'promo_price_and_product_matching_sales_rub': h['extended_sale_window']['strict_sales_rub'],
        'matching_is_attribution': False, 'causal_effect_identified': False,
    }
    m['attribution'].update({'research_engine_status': 'Реализовано частично',
                              'rules_compared': a['model_count'], 'windows_days': a['windows_days'],
                              'model_window_runs_real_and_synthetic': a['comparison_runs'],
                              'all_real_models_zero_coverage': a['real_all_models_and_windows_have_zero_coverage'],
                              'history_registry_publications': a['history_registry']['placements']})
    (out/'metrics.json').write_text(json.dumps(m,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    subprocess.run([sys.executable,str(root/'budget.py'),'--metrics',str(out/'metrics.json'),
                    '--out',str(out/'budget')],check=True,stdout=subprocess.DEVNULL)
    print(json.dumps({'metrics': str(out/'metrics.json'),'rows': m['sales']['rows'],
                      'amount_rub': m['sales']['amount_rub'],
                      'historical_romi_pct': m['attribution']['historical_romi_pct']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
