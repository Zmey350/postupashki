import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import numpy as np
from postupashki_ml.service import ForecastService,Economics
from postupashki_ml.synthetic import generate
from postupashki_ml.contracts import read_connection

ROOT=Path(__file__).parents[2]
DB=ROOT/'data/demo.sqlite3'
MODELS=ROOT/'ml/models'


@unittest.skipUnless((MODELS/'purchase.joblib').exists() and DB.exists(),'Run demo and train first')
class Integration(unittest.TestCase):
    def setUp(self):self.s=ForecastService(DB,MODELS)
    def test_batch_invariance_and_identity(self):
        ids=list(range(1,101))
        a=self.s.customers('ml_course','2026-09-01',{},user_ids=ids,batch_size=11,include_rows=True,economics=Economics(fixed_cost_minor=200000))
        b=self.s.customers('ml_course','2026-09-01',{},user_ids=ids,batch_size=100,include_rows=True)
        np.testing.assert_allclose([r['p_scenario'] for r in a['rows']],[r['p_scenario'] for r in b['rows']],rtol=1e-12)
        self.assertAlmostEqual(a['difference_buyers'],0)
        self.assertAlmostEqual(a['difference_contribution_after_campaign_cost_minor'],0)
        self.assertTrue(all(0<=r['p_scenario']<=1 for r in a['rows']))
    def test_price_cap_and_economic_monotonicity(self):
        a=self.s.quote('ch0','ml_course','2026-09-01',incrementality=.7,economics=Economics(required_roi=0))
        b=self.s.quote('ch0','ml_course','2026-09-01',incrementality=.7,economics=Economics(required_roi=1))
        self.assertAlmostEqual(a['maximum_price_minor']/2,b['maximum_price_minor'])
        self.assertLessEqual(a['new_buyers'],a['new_users'])
        c=self.s.quote('ch0','ml_course','2026-09-01')
        self.assertIsNone(c['maximum_price_minor'])
    def test_synthetic_cannot_silently_score_real_database(self):
        con=sqlite3.connect(':memory:')
        with sqlite3.connect(DB) as source:source.backup(con)
        con.execute("UPDATE ml_meta SET value='real' WHERE key='provenance'");con.commit()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'real.sqlite3';p.write_bytes(con.serialize())
            with self.assertRaisesRegex(ValueError,'происхождени'):
                ForecastService(p,MODELS).customers('ml_course','2026-09-01')
        con.close()
    def test_unknown_cost_not_zero(self):
        con=sqlite3.connect(':memory:')
        with sqlite3.connect(DB) as source:source.backup(con)
        con.execute('UPDATE course_versions SET variable_cost_minor=NULL');con.commit()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'missing_cost.sqlite3';p.write_bytes(con.serialize())
            q=ForecastService(p,MODELS).quote('ch0','ml_course','2026-09-01',incrementality=.7)
            self.assertIsNone(q['maximum_price_minor'])
        con.close()
    def test_demo_refuses_existing_database(self):
        with self.assertRaises(FileExistsError):generate(DB)


if __name__=='__main__':unittest.main()
