from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta
import csv
import json
import unittest
from analysis.__main__ import aggregate, FIELDS
from analysis import forecast as f


class AggregateTests(unittest.TestCase):
    def test_package_money_repeat_and_no_customer_export(self):
        with TemporaryDirectory() as td:
            p=Path(td); source=p/'sales.csv'
            rows=[['private_fixture_a','5 463,33','A','01.08.2026 12:00:00'],
                  ['private_fixture_a','5 463,33','B','01.08.2026 12:00:00'],
                  ['private_fixture_a','5 463,34','C','01.08.2026 12:00:00'],
                  ['private_fixture_a','100,01','A','03.08.2026 13:00:00'],
                  ['private_fixture_b','200,00','B','03.08.2026 14:00:00']]
            with source.open('w',encoding='utf-8',newline='') as stream:
                w=csv.writer(stream);w.writerow(FIELDS);w.writerows(rows)
            result=aggregate(source,p/'out')
            self.assertEqual(result['sales']['amount_cents'],1669001)
            self.assertEqual(result['sales']['inferred_orders'],3)
            self.assertEqual(result['sales']['bundle_orders'],1)
            self.assertEqual(result['sales']['repeat_buyers'],1)
            self.assertEqual(result['sales']['repeat_orders'],1)
            self.assertEqual(result['quality']['unobserved_calendar_days'],1)
            with (p/'out/daily.csv').open(encoding='utf-8-sig') as stream:
                daily=list(csv.DictReader(stream))
            self.assertEqual(daily[1]['amount_cents'],'')
            for file in (p/'out').iterdir():
                text=file.read_text(encoding='utf-8-sig')
                self.assertNotIn('private_fixture_',text)
                self.assertNotIn('12:00:00',text)

    def test_duplicate_quality_does_not_silently_drop_sales(self):
        with TemporaryDirectory() as td:
            p=Path(td);source=p/'sales.csv'
            row=['private_fixture','10,00','A','01.08.2026 12:00:00']
            with source.open('w',encoding='utf-8',newline='') as stream:
                w=csv.writer(stream);w.writerow(FIELDS);w.writerows([row,row])
            result=aggregate(source,p/'out')
            self.assertEqual(result['quality']['exact_duplicates'],1)
            self.assertEqual(result['sales']['amount_cents'],2000)
            self.assertEqual(result['sales']['bundle_orders'],0)

    def test_forecast_selection_never_sees_holdout_outcomes(self):
        y=[30,12,20,80,10,5,13]*6
        dates=[(datetime(2026,1,1)+timedelta(days=i)).date().isoformat() for i in range(len(y))]
        original=f.select_model(y,dates)
        changed=f.select_model(y[:-7]+[10**9]*7,dates)
        self.assertEqual(original,changed)
        for row in original[2]:
            self.assertLess(row['train_end'],row['origin'])
            self.assertLess(row['date'],dates[-7])

    def test_weekly_accuracy_is_not_daily_accuracy(self):
        y=[10,20,30,40,50,60,70]*3
        dates=[(datetime(2026,1,1)+timedelta(days=i)).date().isoformat() for i in range(21)]
        _, folds, _=f.evaluate(y,dates,[14],'test','test')
        a=next(r for r in folds if r['model']=='seasonal7')
        b=next(r for r in folds if r['model']=='mean7')
        self.assertEqual(a['absolute_total_error'],b['absolute_total_error'])
        self.assertGreater(b['daily_absolute_error_sum'],a['daily_absolute_error_sum'])


if __name__=='__main__':
    unittest.main()
