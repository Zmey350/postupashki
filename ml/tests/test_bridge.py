import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from ml_bridge import status,predict_customers,predict_channels
from postupashki_ml.contracts import provenance,initialize_ml
from postupashki_data.db import connect,initialize

ROOT=Path(__file__).parents[2]


class ProvenanceTests(unittest.TestCase):
    def test_unknown_source_not_assumed_real(self):
        with sqlite3.connect(':memory:') as con:
            self.assertEqual(provenance(con),'unknown')

    def test_additive_migration_inherits_synthetic_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'database.sqlite3'
            with connect(db) as con:
                initialize(con)
                con.executescript("CREATE TABLE app_meta (key TEXT PRIMARY KEY,value TEXT);INSERT INTO app_meta VALUES ('provenance','synthetic');")
            initialize_ml(db)
            with sqlite3.connect(db) as con:
                self.assertEqual(provenance(con),'synthetic')

    def test_conflicting_sources_fail(self):
        with sqlite3.connect(':memory:') as con:
            con.executescript("CREATE TABLE app_meta (key TEXT,value TEXT);CREATE TABLE ml_meta (key TEXT,value TEXT);INSERT INTO app_meta VALUES ('provenance','real');INSERT INTO ml_meta VALUES ('provenance','synthetic');")
            with self.assertRaisesRegex(ValueError,'Конфликт'):provenance(con)


@unittest.skipUnless((ROOT/'ml/models/new_buyers.joblib').exists(),'Run demo and train first')
class BridgeTests(unittest.TestCase):
    def test_same_db_read_only_aggregate_and_explicit_unknown_economics(self):
        db=ROOT/'data/demo.sqlite3';models=ROOT/'ml/models'
        with sqlite3.connect(db) as con:before=con.execute('SELECT count(*) FROM orders').fetchone()
        info=status(db,models)
        self.assertTrue(info['available'],info['reason'])
        self.assertEqual(info['provenance'],'synthetic')
        score=predict_customers(db,models,{'as_of':'2026-09-01'})
        self.assertNotIn('rows',score)
        self.assertEqual(score['difference_buyers'],0)
        quote=predict_channels(db,models,{'channel_id':'ch0','as_of':'2026-09-01'})
        self.assertIsNone(quote['maximum_price_minor'])
        json.dumps(score,allow_nan=False);json.dumps(quote,allow_nan=False)
        with sqlite3.connect(db) as con:self.assertEqual(before,con.execute('SELECT count(*) FROM orders').fetchone())


if __name__=='__main__':unittest.main()
