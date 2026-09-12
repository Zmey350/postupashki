"""Focused tests for the denominator, maturity, unknown costs and economic accounting."""
from pathlib import Path
from copy import deepcopy
import csv
import json
import sys
import tempfile
import unittest

from analysis import experiment as e
from analysis import budget as p


class CausalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.result = e.demo(cls.root)
        cls.a = e.read_csv(cls.root/'assignments.csv')
        cls.o = e.read_csv(cls.root/'outcomes.csv')
        cls.c = json.loads((cls.root/'config.json').read_text())
        cls.asof = '2026-08-22T00:00:00+00:00'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_randomization_is_reproducible_and_not_order_dependent(self):
        ids = [r['user_key'] for r in self.a]
        self.assertEqual(e.assign(ids, self.c, 1707), e.assign(ids[::-1], self.c, 1707))
        self.assertEqual(sum(r['arm']=='T' for r in self.a), 1000)
        with self.assertRaises(ValueError):
            e.assign(ids+[ids[0]], self.c, 1707)

    def test_nonbuyers_stay_and_attribution_does_not_become_incrementality(self):
        r = self.result
        self.assertEqual((r['n_t'], r['n_c']), (1000, 1000))
        self.assertAlmostEqual(r['metrics']['paid_buyer']['difference'], .01)
        self.assertEqual(r['metrics']['net_revenue']['difference'], 100)
        self.assertEqual(r['metrics']['contribution']['difference'], 60)
        self.assertEqual(r['economic_decision']['incremental_profit_rub'], 0)
        self.assertLess(r['economic_decision']['profit_ci_low'], 0)
        self.assertGreater(r['economic_decision']['profit_ci_high'], 0)
        self.assertFalse(r['economic_decision']['release_budget'])
        self.assertFalse(r['economic_decision']['paid_acquisition_transfer_supported'])

    def test_missing_person_is_not_silently_a_zero(self):
        for rows in (self.o[1:], self.o+[self.o[0]]):
            with self.assertRaisesRegex(ValueError, 'Every assigned'):
                e.analyze(self.a, rows, self.c, self.asof)
        bad = deepcopy(self.o)
        bad[0]['complete'] = '0'
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            e.analyze(self.a, bad, self.c, self.asof)

    def test_window_and_before_contact_checks(self):
        with self.assertRaisesRegex(ValueError, 'not mature'):
            e.analyze(self.a, self.o, self.c, '2026-08-21T23:59:59+00:00')
        c = {**self.c, 'assignment_before_contact_confirmed': False}
        with self.assertRaisesRegex(ValueError, 'before contact'):
            e.analyze(self.a, self.o, c, self.asof)
        bad = deepcopy(self.o)
        bad[0]['window_end'] = '2026-08-16T00:00:00+00:00'
        with self.assertRaisesRegex(ValueError, 'Wrong outcome window'):
            e.analyze(self.a, bad, self.c, self.asof)

    def test_unknown_and_zero_costs_differ(self):
        bad = deepcopy(self.o)
        bad[0]['variable_cost_cents'] = ''
        r = e.analyze(self.a, bad, self.c, self.asof)
        self.assertEqual(r['economic_decision']['status'], 'costs_unknown')
        self.assertNotIn('contribution', r['metrics'])
        c = {**self.c, 'marketing_cost_t_cents': None}
        self.assertEqual(e.analyze(self.a, self.o, c, self.asof)['economic_decision']['status'], 'costs_unknown')
        c = {**self.c, 'marketing_cost_t_cents': 0}
        r = e.analyze(self.a, self.o, c, self.asof)
        self.assertEqual(r['economic_decision']['incremental_profit_rub'], 60000)
        self.assertIsNone(r['economic_decision']['ROMI_incremental_contribution'])

    def test_refund_is_subtracted_and_randomization_unit_not_payment(self):
        bad = deepcopy(self.o)
        target = next(r for r in self.a if r['arm']=='T' and next(o for o in bad if o['user_key']==r['user_key'])['paid_buyer']=='1')
        row = next(r for r in bad if r['user_key']==target['user_key'])
        row['refund_cents'] = row['gross_paid_cents']
        r = e.analyze(self.a, bad, self.c, self.asof)
        self.assertEqual(r['n_t'], 1000)
        self.assertEqual(r['metrics']['net_revenue']['difference'], 90)
        self.assertEqual(r['economic_decision']['incremental_profit_rub'], -10000)
        with self.assertRaisesRegex(ValueError, 'clustered'):
            e.analyze(self.a, self.o, {**self.c, 'randomization_unit':'channel'}, self.asof)

    def test_power_monotonicity_and_zero_variance_guard(self):
        self.assertEqual(p.sample_size(.02,.03),3826)
        self.assertGreater(p.sample_size(.02,.024),p.sample_size(.02,.03))
        self.assertGreater(p.sample_size(.02,.03,power=.9),p.sample_size(.02,.03))
        self.assertGreater(p.binary_mde(.02,500),p.binary_mde(.02,1000))
        r = e.difference([0]*100, [0]*100)
        self.assertIsNone(r['ci_low'])
        self.assertIsNone(r['ci_high'])


if __name__ == '__main__':
    unittest.main()
