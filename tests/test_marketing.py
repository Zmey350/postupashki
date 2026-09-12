"""Вымышленные события только для тестов; рабочую БД не заполняют."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from postupashki_data import connect, initialize, ingest, build_features, build_marketing, export_marketing, utc
from postupashki_data.db import record
from postupashki_data.catalog import RAW_TABLES
from test_data import fixture, event, T


def marketing_fixture():
    p = fixture()
    p['collection_periods'].append(dict(period_id='ad-costs',domain='ad_costs',starts_at='2025-01-01',ends_at=T,recorded_at=T))
    p['collection_periods'] += [dict(period_id=d,domain=d,starts_at='2025-02-01',ends_at=T,recorded_at=T)
                               for d in ('promotion_offers','promotion_responses','order_promotions')]
    p.update({
        'channel_snapshots':[dict(snapshot_id='s1',channel_id='ext_a',observed_at='2025-02-01',recorded_at='2025-02-01',
                                 subscribers=1000,typical_post_views_24h=400,audience_stage='university',source='owner_report')],
        'placement_metrics':[dict(metric_id='v1',placement_id='p1',observed_at='2025-01-02',recorded_at='2025-01-02',views=400,source='owner_report')],
        'placement_cost_events':[dict(cost_event_id='cost1',placement_id='p1',kind='expense',amount_minor=500000,
                                     occurred_at='2025-01-01',recorded_at='2025-01-01')],
        'promotions':[dict(promotion_id='spring',title='Весенняя акция',kind='discount',created_at='2025-02-01',recorded_at='2025-02-01')],
        'promotion_versions':[dict(version_id='spring-v1',promotion_id='spring',effective_at='2025-02-28',recorded_at='2025-02-28',
                                  starts_at='2025-03-01',ends_at='2025-04-05',discount_type='percent',discount_value=20,terms='Курсы весны')],
        'promotion_courses':[dict(link_id='pc'+str(i),version_id='spring-v1',course_id=c) for i,c in enumerate(['c3','c4'])],
        'promotion_offers':[dict(offer_id='of'+str(i),version_id='spring-v1',assignment_reason='segment',recorded_at=dt)
                            for i,dt in [(1,'2025-03-03'),(2,'2025-03-25'),(3,'2025-03-26'),(4,'2025-03-27')]],
        'promotion_responses':[dict(response_id=k,offer_id='of1',kind='clicked',occurred_at='2025-03-04',recorded_at='2025-03-04') for k in ('click1','click2')],
        'order_promotions':[dict(order_id='o2',version_id='spring-v1',evidence='promo_code',recorded_at='2025-03-02'),
                            dict(order_id='o3',version_id='spring-v1',offer_id='of1',evidence='offer_checkout',recorded_at='2025-03-20')],
    })
    return p


class MarketingTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        initialize(self.db)

    def tearDown(self):
        self.db.close()

    def row(self, table='channel_stats', key='ext_a', at=T, days=30):
        build_marketing(self.db,at,days)
        col = 'channel_id' if table=='channel_stats' else 'promotion_id'
        return dict(self.db.execute(f'SELECT * FROM {table} WHERE {col}=? AND as_of=? AND horizon_days=?',(key,utc(at),days)).fetchone())

    def test_first_source_churn_rejoin_and_window_money(self):
        ingest(self.db,marketing_fixture())
        r = self.row()
        for key, value in dict(first_source_users=1,touch_users=1,mature_users=1,buyers_horizon=1,
                               conversion_horizon=1,net_cash_horizon_minor=100000,member_observed_users=1,
                               left_users=1,rejoined_users=1,current_members=1,retention_horizon=1,
                               ad_cost_minor=500000,net_cash_to_date_minor=305000).items():
            self.assertEqual(r[key],value,key)
        self.assertIsNone(r['attributed_romi'])  # неизвестная себестоимость и аванс
        b = self.row(key='ext_b')
        self.assertEqual(b['first_source_users'],0)
        self.assertEqual(b['returning_touch_users'],1)
        self.assertEqual(b['net_cash_to_date_minor'],None)  # нет полного основания для нуля

    def test_promotion_deduplicates_clicks_and_does_not_attribute_by_dates(self):
        ingest(self.db,marketing_fixture())
        r = self.row('promotion_stats','spring',days=20)
        self.assertEqual(r['sent_offers'],3)
        self.assertEqual(r['sent_users'],1)
        self.assertEqual(r['failed_offers'],1)
        self.assertEqual(r['clicked_users'],1)
        self.assertEqual(r['repeat_sent_users'],1)
        self.assertEqual(r['linked_paid_orders'],1)
        self.assertEqual(r['linked_buyers'],1)
        self.assertEqual(r['net_cash_minor'],225000)
        self.assertEqual(r['conversion_horizon'],0)  # o2 оформлен до первой отправки, o3 пока аванс
        self.assertEqual(r['duration_days'],35)
        self.assertEqual(r['days_to_end'],4)
        self.assertEqual(r['is_active'],1)

    def test_immature_cohort_unknown_sources_and_unknown_membership(self):
        p = marketing_fixture()
        p['users'] += [dict(user_id=i,first_seen_at='2025-03-25',recorded_at='2025-03-25') for i in (2,3)]
        p['acquisition_events'].append(dict(event_id='new',user_id=2,source_channel_id='ext_a',placement_id='p1',
                                           mechanism='bot_start',occurred_at='2025-03-25',recorded_at='2025-03-25'))
        ingest(self.db,p)
        r = self.row()
        self.assertEqual(r['first_source_users'],2)
        self.assertEqual(r['mature_users'],1)
        self.assertEqual(r['current_membership_unknown_users'],1)
        self.assertEqual(r['conversion_horizon'],1)
        self.assertEqual(self.row(key='__unknown__')['first_source_users'],1)

    def test_observation_gap_does_not_become_zero_or_false_retention(self):
        p = marketing_fixture()
        p['collection_periods'] = [r for r in p['collection_periods'] if r['domain'] not in ('commerce','membership')]
        ingest(self.db,p)
        r = self.row()
        self.assertIsNone(r['conversion_horizon'])
        self.assertIsNone(r['net_cash_horizon_minor'])
        self.assertIsNone(r['retention_horizon'])
        self.assertEqual(r['current_membership_unknown_users'],1)
        self.assertEqual(r['commerce_observed_users'],0)

    def test_late_records_and_future_versions_do_not_leak(self):
        ingest(self.db,marketing_fixture())
        before_c = self.row()
        before_p = self.row('promotion_stats','spring')
        ingest(self.db,{
            'channel_snapshots':[dict(snapshot_id='late',channel_id='ext_a',observed_at='2025-03-01',recorded_at='2025-04-02',subscribers=99999,source='manual')],
            'promotion_versions':[dict(version_id='spring-v2',promotion_id='spring',effective_at='2025-04-02',recorded_at='2025-03-30',
                                       starts_at='2025-03-01',ends_at='2025-05-01',discount_type='percent',discount_value=50,terms='Продление')],
            'promotion_courses':[dict(link_id='late-c',version_id='spring-v2',course_id='c3')],
            'payments':[dict(payment_id='late-ref',order_id='o2',kind='refund',amount_minor=200000,occurred_at='2025-03-30',recorded_at='2025-04-02')]
        })
        self.assertEqual(before_c,self.row())
        self.assertEqual(before_p,self.row('promotion_stats','spring'))

    def test_attributed_romi_requires_costs_and_fees_and_handles_refunds(self):
        p = marketing_fixture()
        for r in p['payments']:
            r['fee_minor'] = 0
        for r in p['order_items']:
            r['variable_cost_minor'] = 1000
        p['payments'].append(dict(payment_id='finish',order_id='o3',kind='payment',amount_minor=25000,fee_minor=0,occurred_at='2025-03-21',recorded_at='2025-03-21'))
        ingest(self.db,p)
        r = self.row()
        self.assertEqual(r['net_cash_to_date_minor'],330000)
        self.assertEqual(r['contribution_to_date_minor'],326000)
        self.assertAlmostEqual(r['attributed_romi'],-0.348)
        ingest(self.db,{'placement_cost_events':[dict(cost_event_id='refund-ad',placement_id='p1',kind='refund',amount_minor=100000,occurred_at='2025-03-21',recorded_at='2025-03-21')]})
        self.assertAlmostEqual(self.row()['attributed_romi'],-0.185)

    def test_links_constraints_and_atomic_rollback(self):
        ingest(self.db,marketing_fixture())
        with self.assertRaises(ValueError):
            ingest(self.db,{'promotion_courses':[dict(link_id='append',version_id='spring-v1',course_id='c5')]})
        bad = dict(response_id='failed',offer_id='of3',kind='clicked',occurred_at='2025-03-27',recorded_at='2025-03-27')
        with self.assertRaises(ValueError):
            ingest(self.db,{'promotion_responses':[bad]})
        with self.assertRaises(ValueError):
            ingest(self.db,{'placement_cost_events':[dict(cost_event_id='overrefund',placement_id='p1',kind='refund',amount_minor=500001,occurred_at='2025-03-21',recorded_at='2025-03-21')]})
        with self.assertRaises(ValueError):
            ingest(self.db,{'order_promotions':[dict(order_id='o1',version_id='spring-v1',evidence='promo_code',recorded_at='2025-03-01')]})
        self.assertEqual(self.row('promotion_stats','spring')['net_cash_minor'],225000)
        self.assertEqual(ingest(self.db,marketing_fixture())['inserted'],0)

    def test_v1_coverage_does_not_claim_marketing_completeness(self):
        p = marketing_fixture()
        p['collection_periods'] = [r for r in p['collection_periods'] if r['domain'] not in
                                  ('ad_costs','promotion_offers','promotion_responses','order_promotions')]
        ingest(self.db,p)
        self.assertIsNone(self.row()['ad_cost_minor'])
        r = self.row('promotion_stats','spring',days=20)
        self.assertIsNone(r['click_rate'])
        self.assertIsNone(r['conversion_horizon'])
        self.assertEqual(r['clicked_users'],1)

    def test_tracking_route_mismatch_rolls_back(self):
        p = marketing_fixture()
        p['acquisition_events'][0]['source_token'] = 'p1token'
        p['tracking_links'] = [dict(link_id='link',source_token='p1token',source_channel_id='ext_b',
                                   destination_channel_id='main',mechanism='invite_link',created_at='2025-01-01',recorded_at='2025-01-01')]
        with self.assertRaises(ValueError):
            ingest(self.db,p)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM users').fetchone()[0],0)

    def test_migration_preserves_v1_rows_and_snapshots(self):
        # Исходная схема v1 поставляется в пакете, чтобы миграцию можно было повторить.
        db = connect(':memory:')
        db.executescript(Path('postupashki_data/schema.sql').read_text())
        for table in RAW_TABLES:
            for r in fixture().get(table,[]):
                record(db,table,r)
        db.commit()
        build_features(db,T)
        snapshot = dict(db.execute('SELECT * FROM user_features').fetchone())
        initialize(db)
        initialize(db)
        self.assertEqual(db.execute('SELECT version FROM schema_meta').fetchone()[0],2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],5)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
        self.assertEqual(snapshot,dict(db.execute('SELECT * FROM user_features').fetchone()))
        self.assertEqual(build_marketing(db,T)['channel_stats'],3)
        db.close()

    def test_rebuild_export_and_explicit_main_channel(self):
        ingest(self.db,marketing_fixture())
        self.row()
        self.row()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM channel_stats').fetchone()[0],3)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/'channels.csv'
            export_marketing(self.db,'channel_stats',T,f)
            self.assertEqual(len(f.read_text().splitlines()),4)
        ingest(self.db,{'channels':[dict(channel_id='main2',title='Второй',kind='own_main',created_at='2025-01-01',recorded_at='2025-01-01')]})
        with self.assertRaises(ValueError):
            build_marketing(self.db,T)
        build_marketing(self.db,T,main_channel_id='main')

    def test_ban_is_not_voluntary_exit_and_status_checks_are_not_rejoins(self):
        p = marketing_fixture()
        p['membership_events'][1]['status'] = 'banned'
        p['membership_events'][2]['evidence_kind'] = 'status_check'
        ingest(self.db,p)
        r = self.row()
        self.assertEqual(r['left_users'],0)
        self.assertEqual(r['banned_users'],1)
        self.assertEqual(r['rejoined_users'],0)
        self.assertEqual(r['current_members'],1)


if __name__=='__main__':
    unittest.main()
