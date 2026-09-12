"""Только вымышленные проверочные записи. В рабочую БД они не загружаются."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from postupashki_data import connect, initialize, ingest, build_features, export_features, utc
from postupashki_data.catalog import FEATURE_NAMES, RAW_TABLES

T = '2025-04-01'


def event(eid, date, **kwargs):
    return dict(event_id=eid,user_id=1,occurred_at=date,recorded_at=date,**kwargs)


def fixture():
    """Маленький контрольный набор с вручную проверяемой арифметикой."""
    p = {
        'topics':[dict(topic_id='ml',title='ML'),dict(topic_id='math',title='Математика')],
        'channels':[dict(channel_id=k,title=k,kind=kind,created_at='2025-01-01',recorded_at='2025-01-01')
                    for k,kind in [('main','own_main'),('ext_a','external'),('ext_b','external')]],
        'users':[dict(user_id=1,first_seen_at='2025-01-01',recorded_at='2025-01-01')],
        'courses':[dict(course_id=k,topic_id=topic,title=k,created_at='2025-01-01',recorded_at='2025-01-01')
                   for k,topic in [('c1','ml'),('c2','math'),('c3','ml'),('c4','math'),('c5','ml')]],
        'course_versions':[
            dict(version_id='v'+k,course_id=k,effective_at='2025-01-01',recorded_at='2025-01-01',
                 sales_open_at='2025-03-01',sales_close_at='2025-04-20',starts_at=start,regular_price_minor=100000)
            for k,start in [('c3','2025-04-05'),('c4','2025-04-05'),('c5','2025-04-10')]],
        'placements':[dict(placement_id='p1',channel_id='ext_a',published_at='2025-01-01',
                           recorded_at='2025-01-01',quoted_cost_minor=500000)],
        'profile_events':[
            event('edu1','2025-01-02',field='education_stage',value='school',source='bot_form'),
            event('edu2','2025-02-01',field='education_stage',value='university',source='bot_form'),
            event('job1','2025-03-01',field='job_search_status',value='internship',source='bot_form')],
        'acquisition_events':[
            event('a1','2025-01-02',source_channel_id='ext_a',placement_id='p1',mechanism='invite_link'),
            event('a2','2025-03-10',source_channel_id='ext_b',mechanism='bot_start')],
        'membership_events':[
            event('m1','2025-01-02',channel_id='main',status='joined'),
            event('m2','2025-03-10',channel_id='main',status='left'),
            event('m3','2025-03-20',channel_id='main',status='joined')],
        'activity_events':[
            event('x1','2025-03-10',event_type='topic_selected',topic_id='ml'),
            event('x2','2025-03-10T12:00:00Z',event_type='topic_selected',topic_id='ml'),
            event('x3','2025-03-20',event_type='topic_selected',topic_id='math'),
            event('x4','2025-03-29',event_type='price_requested',course_id='c3'),
            event('x5','2025-03-30',event_type='course_program_requested',course_id='c4'),
            event('x6','2025-03-31',event_type='bot_started')],
        'hackathons':[dict(hackathon_id=k,topic_id='ml',title=k,starts_at='2025-02-10',
                          ends_at='2025-02-12',recorded_at='2025-01-01') for k in ('h1','h2')],
        'participation_events':[
            event('hreg1','2025-02-01',hackathon_id='h1',status='registered'),
            event('hatt1','2025-02-10',hackathon_id='h1',status='attended'),
            event('hsol1','2025-02-11',hackathon_id='h1',status='solution_submitted'),
            event('hreg2','2025-02-01',hackathon_id='h2',status='registered')],
        'orders':[dict(order_id=k,user_id=1,ordered_at=dt,recorded_at=dt,total_minor=amount)
                  for k,dt,amount in [('o1','2025-01-10',100000),('o2','2025-03-02',200000),('o3','2025-03-20',50000)]],
        'order_items':[dict(item_id=k,order_id=oid,course_id=cid,line_total_minor=amount)
                       for k,oid,cid,amount in [('i1','o1','c1',60000),('i2','o1','c2',40000),
                                               ('i3','o2','c3',200000),('i4','o3','c4',50000)]],
        'payments':[dict(payment_id=k,order_id=oid,kind=kind,amount_minor=amount,occurred_at=dt,recorded_at=dt)
                    for k,oid,kind,amount,dt in [('pay1','o1','payment',60000,'2025-01-10'),
                                               ('pay2','o1','payment',40000,'2025-01-11'),
                                               ('ref1','o1','refund',20000,'2025-02-01'),
                                               ('pay3','o2','payment',200000,'2025-03-03'),
                                               ('pay4','o3','payment',25000,'2025-03-20')]],
        'offers':[dict(offer_id=k,user_id=1,course_id=cid,regular_price_minor=100000,
                       offered_price_minor=price,status=status,sent_at=dt,recorded_at=dt,valid_until=end)
                  for k,cid,price,status,dt,end in [
                      ('of1','c4',70000,'sent','2025-03-03','2025-04-05'),
                      ('of2','c4',80000,'sent','2025-03-25','2025-04-03'),
                      ('of3','c4',50000,'failed','2025-03-26','2025-04-03'),
                      ('of4','c3',50000,'sent','2025-03-27','2025-03-29')]],
        'collection_periods':[dict(period_id=d,domain=d,starts_at='2025-01-01',ends_at=T,recorded_at=T)
                              for d in ('acquisition','membership','activity','commerce','participation','offers')]
    }
    return p


class DataTests(unittest.TestCase):
    def setUp(self):
        self.con = connect(':memory:')
        initialize(self.con)

    def tearDown(self):
        self.con.close()

    def seed(self, payload=None):
        ingest(self.con,fixture() if payload is None else payload)

    def snapshot(self, uid=1, date=T):
        build_features(self.con,date)
        return dict(self.con.execute('SELECT * FROM user_features WHERE user_id=? AND as_of=?',(uid,utc(date))).fetchone())

    def test_empty_database_and_exact_twenty_feature_contract(self):
        self.assertEqual(len(FEATURE_NAMES),20)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM feature_definitions').fetchone()[0],20)
        self.assertEqual(sum(self.con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in RAW_TABLES),0)
        self.assertEqual(build_features(self.con,T),0)

    def test_all_twenty_values_against_hand_calculation(self):
        self.seed()
        r=self.snapshot()
        expected=dict(first_source_channel_id='ext_a',education_stage='university',job_search_status='internship',
                      primary_topic_id='ml',days_since_first_seen=90,is_main_channel_member=1,
                      days_since_last_activity=1,active_days_30d=5,orders_count_90d=2,
                      net_spend_90d_minor=305000,mean_order_value_90d_minor=140000,
                      days_since_last_purchase=29,mean_purchase_gap_days=51,paid_courses_count=3,
                      hackathons_attended_180d=1,course_intent_actions_30d=2,offers_sent_30d=3,
                      active_offer_discount_pct=20,calendar_month=4,days_to_next_relevant_course_start=9)
        self.assertEqual(set(expected),set(FEATURE_NAMES))
        for k,value in expected.items():
            with self.subTest(k=k):
                if isinstance(value,(int,float)):
                    self.assertAlmostEqual(r[k],value)
                else:
                    self.assertEqual(r[k],value)

    def test_duplicate_delivery_is_idempotent_and_conflict_is_error(self):
        p=fixture()
        self.seed(p)
        self.assertEqual(ingest(self.con,p)['inserted'],0)
        row=copy.deepcopy(p['activity_events'][0])
        row.pop('recorded_at')
        self.assertEqual(ingest(self.con,{'activity_events':[row]})['duplicates'],1)
        row['topic_id']='math'
        with self.assertRaises(ValueError):
            ingest(self.con,{'activity_events':[row]})
        self.assertEqual(self.snapshot()['active_days_30d'],5)

    def test_future_and_late_arriving_information_do_not_leak(self):
        self.seed()
        before=self.snapshot()
        late=event('late-profile','2025-03-05',field='job_search_status',value='job',source='bot_form')
        late['recorded_at']='2025-04-03'
        ingest(self.con,{
            'profile_events':[late,event('future-profile','2025-04-02',field='education_stage',value='graduate',source='bot_form')],
            'activity_events':[event('boundary',T,event_type='topic_selected',topic_id='math')],
            'payments':[dict(payment_id='late-refund',order_id='o2',kind='refund',amount_minor=200000,
                             occurred_at='2025-03-30',recorded_at='2025-04-02')],
            'course_versions':[dict(version_id='late-plan',course_id='c5',effective_at='2025-03-01',recorded_at='2025-04-02',
                                    sales_open_at='2025-03-01',sales_close_at='2025-04-02',starts_at='2025-04-02',regular_price_minor=50000)]})
        after=self.snapshot()
        for k in [*FEATURE_NAMES,'quality_json']:
            self.assertEqual(before[k],after[k],k)

    def test_missing_coverage_is_not_zero_and_state_needs_evidence(self):
        p=fixture()
        p['users'].append(dict(user_id=2,first_seen_at='2025-03-15',recorded_at='2025-03-15'))
        self.seed(p)
        r=self.snapshot(2)
        self.assertEqual(r['active_days_30d'],0)
        self.assertEqual(r['orders_count_90d'],0)
        self.assertIsNone(r['days_since_last_purchase'])
        self.assertIsNone(r['mean_purchase_gap_days'])
        self.assertIsNone(r['is_main_channel_member'])
        self.con.execute("DELETE FROM collection_periods WHERE domain='activity'")
        self.con.commit()
        r=self.snapshot(2)
        self.assertIsNone(r['active_days_30d'])
        self.assertEqual(json.loads(r['quality_json'])['active_days_30d']['status'],'incomplete_coverage')

    def test_coverage_gaps_are_checked_per_feature_window(self):
        p=fixture()
        p['collection_periods']=[r for r in p['collection_periods'] if r['domain']!='activity']
        p['collection_periods'] += [dict(period_id='activity-a',domain='activity',starts_at='2025-01-01',ends_at='2025-02-01',recorded_at='2025-02-01'),
                                   dict(period_id='activity-b',domain='activity',starts_at='2025-02-02',ends_at=T,recorded_at=T)]
        self.seed(p)
        r=self.snapshot()
        self.assertIsNone(r['primary_topic_id'])
        self.assertEqual(r['active_days_30d'],5)
        self.assertEqual(r['days_since_last_activity'],1)

    def test_full_refund_changes_money_and_ownership_not_purchase_date(self):
        self.seed()
        ingest(self.con,{'payments':[dict(payment_id='full-refund',order_id='o2',kind='refund',amount_minor=200000,
                                        occurred_at='2025-03-25',recorded_at='2025-03-25')]})
        r=self.snapshot()
        self.assertEqual(r['orders_count_90d'],2)
        self.assertEqual(r['net_spend_90d_minor'],105000)
        self.assertEqual(r['mean_order_value_90d_minor'],40000)
        self.assertEqual(r['paid_courses_count'],2)
        self.assertEqual(r['days_since_last_purchase'],29)
        self.assertEqual(r['days_to_next_relevant_course_start'],4)

    def test_unknown_first_source_is_not_replaced_by_later_ad(self):
        p=fixture()
        p['acquisition_events'].append(event('unknown-first','2025-01-01',mechanism='unknown'))
        self.seed(p)
        self.assertIsNone(self.snapshot()['first_source_channel_id'])

    def test_bad_order_rolls_back_entire_batch(self):
        self.seed()
        with self.assertRaises(ValueError):
            ingest(self.con,{'users':[dict(user_id=2,first_seen_at='2025-02-01',recorded_at='2025-02-01')],
                             'orders':[dict(order_id='bad',user_id=2,ordered_at='2025-02-02',recorded_at='2025-02-02',total_minor=100)]})
        self.assertIsNone(self.con.execute('SELECT * FROM users WHERE user_id=2').fetchone())

    def test_money_and_link_integrity(self):
        self.seed()
        for payment in [dict(payment_id='float',order_id='o3',kind='payment',amount_minor=0.5,occurred_at='2025-03-25',recorded_at='2025-03-25'),
                        dict(payment_id='too-much',order_id='o3',kind='payment',amount_minor=25001,occurred_at='2025-03-25',recorded_at='2025-03-25'),
                        dict(payment_id='bad-refund',order_id='o3',kind='refund',amount_minor=25001,occurred_at='2025-03-25',recorded_at='2025-03-25')]:
            with self.subTest(payment=payment['payment_id']),self.assertRaises(ValueError):
                ingest(self.con,{'payments':[payment]})
        wrong=event('wrong-source','2025-03-25',mechanism='invite_link',source_channel_id='ext_b',placement_id='p1')
        with self.assertRaises(sqlite3.IntegrityError):
            ingest(self.con,{'acquisition_events':[wrong]})
        with self.assertRaises(ValueError):
            ingest(self.con,{'order_items':[dict(item_id='zero-extra',order_id='o1',course_id='c5',line_total_minor=0)]})

    def test_free_course_is_not_purchase(self):
        self.seed()
        ingest(self.con,{'orders':[dict(order_id='free',user_id=1,ordered_at='2025-03-31',recorded_at='2025-03-31',total_minor=0)],
                         'order_items':[dict(item_id='free-item',order_id='free',course_id='c5',line_total_minor=0)]})
        r=self.snapshot()
        self.assertEqual(r['orders_count_90d'],2)
        self.assertEqual(r['paid_courses_count'],3)

    def test_refund_before_completion_does_not_create_paid_order(self):
        self.seed()
        ingest(self.con,{'payments':[
            dict(payment_id='partial-refund',order_id='o3',kind='refund',amount_minor=25000,
                 occurred_at='2025-03-21',recorded_at='2025-03-21'),
            dict(payment_id='partial-again',order_id='o3',kind='payment',amount_minor=25000,
                 occurred_at='2025-03-22',recorded_at='2025-03-22')]})
        r=self.snapshot()
        self.assertEqual(r['orders_count_90d'],2)
        self.assertEqual(r['paid_courses_count'],3)

    def test_repayment_after_refund_keeps_one_order(self):
        self.seed()
        ingest(self.con,{'payments':[
            dict(payment_id='refund-again',order_id='o2',kind='refund',amount_minor=200000,
                 occurred_at='2025-03-21',recorded_at='2025-03-21'),
            dict(payment_id='repay',order_id='o2',kind='payment',amount_minor=200000,
                 occurred_at='2025-03-22',recorded_at='2025-03-22')]})
        r=self.snapshot()
        self.assertEqual(r['orders_count_90d'],2)
        self.assertEqual(r['mean_order_value_90d_minor'],140000)

    def test_expired_replacement_does_not_revive_previous_offer(self):
        self.seed()
        ingest(self.con,{'offers':[dict(offer_id='replacement',user_id=1,course_id='c4',regular_price_minor=100000,
                                        offered_price_minor=90000,status='sent',sent_at='2025-03-28',recorded_at='2025-03-28',valid_until='2025-03-29')]})
        self.assertIsNone(self.snapshot()['active_offer_discount_pct'])

    def test_half_open_windows_and_timezone_normalization(self):
        self.seed()
        ingest(self.con,{'activity_events':[
            event('left-edge','2025-03-02',event_type='bot_started'),
            event('too-old','2025-03-01T23:59:59Z',event_type='bot_started'),
            event('same-last-day','2025-04-01T02:00:00+03:00',event_type='bot_started'),
            event('right-edge','2025-04-01',event_type='bot_started')]})
        r=self.snapshot()
        self.assertEqual(r['active_days_30d'],6)
        self.assertAlmostEqual(r['days_since_last_activity'],1/24)
        with self.assertRaises(ValueError):
            utc('2025-04-01T12:00:00')

    def test_rebuild_replaces_only_selected_snapshot_and_csv_has_no_targets(self):
        self.seed()
        build_features(self.con,'2025-03-01')
        build_features(self.con,T)
        build_features(self.con,T)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM user_features').fetchone()[0],2)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'features.csv'
            export_features(self.con,T,path)
            self.assertEqual(path.read_text().splitlines()[0].split(','),['user_id','as_of','feature_version',*FEATURE_NAMES])


if __name__=='__main__':
    unittest.main()
