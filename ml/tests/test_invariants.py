import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from postupashki_data.db import connect,initialize
from postupashki_ml.contracts import stamp,after,paid_orders,covered,Plan
from postupashki_ml.decay import Decay
from postupashki_ml.customers import CustomerContext,decisions
from postupashki_ml.channels import ChannelHistory,portfolio_estimate
from postupashki_ml.search import time_splits


class Fixture(unittest.TestCase):
    def setUp(self):
        self.c=connect(':memory:');initialize(self.c)
        self.c.executescript((Path(__file__).parents[2]/'postupashki_ml/schema.sql').read_text())
        self.t=stamp('2026-01-01');self.now=after(self.t,100)
        self.add('topics',topic_id='t',title='T')
        for cid,kind in [('main','own_main'),('a','external'),('b','external')]:
            self.add('channels',channel_id=cid,title=cid,kind=kind,topic_id='t',created_at=self.t,recorded_at=self.t)
        self.add('courses',course_id='c',topic_id='t',title='C',created_at=self.t,recorded_at=self.t)
        self.add('course_versions',version_id='v',course_id='c',effective_at=self.t,recorded_at=self.t,sales_open_at=self.t,sales_close_at=after(self.t,300),starts_at=after(self.t,320),regular_price_minor=10000,variable_cost_minor=2000)
        self.add('users',user_id=1,first_seen_at=self.t,recorded_at=self.t)
        for d in ('commerce','activity','offers','acquisition','membership','participation'):
            self.add('collection_periods',period_id=d,domain=d,starts_at=self.t,ends_at=self.now,recorded_at=self.now)
        self.add('ml_contact_periods',period_id='cp',starts_at=self.t,ends_at=self.now,recorded_at=self.now)
    def add(self,table,**r):
        self.c.execute('INSERT INTO '+table+' ('+','.join(r)+') VALUES ('+','.join('?' for _ in r)+')',list(r.values()))
    def tearDown(self):self.c.close()
    def order(self,oid='o',total=10000):
        self.add('orders',order_id=oid,user_id=1,ordered_at=after(self.t,10),recorded_at=after(self.t,10),total_minor=total)
        self.add('order_items',item_id=oid+'i',order_id=oid,course_id='c',quantity=1,line_total_minor=total)
    def payment(self,pid,amount,day,kind='payment',recorded=None):
        self.add('payments',payment_id=pid,order_id='o',kind=kind,amount_minor=amount,occurred_at=after(self.t,day),recorded_at=after(self.t,recorded if recorded is not None else day))

    def test_partial_settlement_and_refund(self):
        self.order();self.payment('p1',4000,11);self.payment('p2',6000,12);self.payment('r',10000,13,'refund')
        self.assertEqual(len(paid_orders(self.c,after(self.t,12))),0)
        p=paid_orders(self.c,after(self.t,14))
        self.assertEqual(len(p),1);self.assertEqual(p.iloc[0].paid_at,after(self.t,12));self.assertEqual(p.iloc[0].balance_minor,0)
    def test_simultaneous_payment_and_refund_do_not_create_purchase(self):
        self.order();self.payment('p',10000,11);self.payment('r',10000,11,'refund')
        self.assertEqual(len(paid_orders(self.c,self.now)),0)
    def test_late_payment_not_in_past(self):
        self.order();self.payment('p',10000,11,recorded=20)
        self.assertTrue(paid_orders(self.c,after(self.t,15)).empty)
        self.assertEqual(len(paid_orders(self.c,after(self.t,21))),1)
    def test_late_backfill_and_future_events_do_not_change_features(self):
        d=Decay();before=CustomerContext(self.c,'c',self.now).features(d)[0]
        self.add('profile_events',event_id='late',user_id=1,field='job_search_status',value='interviewing',source='bot_form',occurred_at=after(self.t,80),recorded_at=after(self.now,1))
        self.add('ml_contacts',contact_id='future',user_id=1,course_id='c',kind='message',evidence='delivered',intensity=90,occurred_at=after(self.now,1),recorded_at=after(self.now,1))
        after_frame=CustomerContext(self.c,'c',self.now).features(d)[0]
        assert_frame_equal(before,after_frame)
    def test_current_price_expires_but_warmth_persists(self):
        self.add('offers',offer_id='offer',user_id=1,course_id='c',regular_price_minor=10000,offered_price_minor=5000,status='sent',sent_at=after(self.now,-2),recorded_at=after(self.now,-2),valid_until=after(self.now,1))
        x,pr=CustomerContext(self.c,'c',self.now).features(Decay())
        self.assertEqual(pr.iloc[0].price_min_minor,5000);self.assertEqual(pr.iloc[0].price_max_minor,10000)
        self.assertGreater(x.iloc[0].offer_warm_end,0)
    def test_missing_collection_is_not_zero(self):
        self.c.execute('DELETE FROM ml_contact_periods')
        x,_=CustomerContext(self.c,'c',self.now).features(Decay())
        self.assertTrue(np.isnan(x.iloc[0].message_warm_now));self.assertEqual(x.iloc[0].message_history_complete,0)
    def test_zero_plan_identity_and_future_contacts(self):
        ctx=CustomerContext(self.c,'c',self.now)
        x,_=ctx.features(Decay());z,_=ctx.features(Decay(),{})
        assert_frame_equal(x,z)
        s,_=ctx.features(Decay(),{'contacts':[{'day':5,'kind':'message'}]})
        self.assertEqual(x.iloc[0].message_warm_now,s.iloc[0].message_warm_now)
        self.assertGreater(s.iloc[0].message_warm_mean,x.iloc[0].message_warm_mean)
    def test_horizon_coverage_and_gaps(self):
        self.assertFalse(covered(self.c,'commerce',after(self.now,-1),after(self.now,1),after(self.now,2)))
        self.assertFalse(covered(self.c,'commerce',self.t,self.now,after(self.now,-1)))
        self.assertTrue(covered(self.c,'commerce',self.t,self.now,self.now))
    def test_acquisition_deduplicated_across_channels(self):
        for cid,day in [('a',10),('b',11)]:
            self.add('placements',placement_id=cid,channel_id=cid,published_at=after(self.t,day),recorded_at=after(self.t,day),advertised_course_id='c')
        self.add('users',user_id=2,first_seen_at=after(self.t,10.1),recorded_at=after(self.t,10.1))
        for i,(cid,day) in enumerate([('a',10.1),('a',10.2),('b',11.1)]):
            self.add('acquisition_events',event_id=str(i),user_id=2,source_channel_id=cid,placement_id=cid,mechanism='bot_start',occurred_at=after(self.t,day),recorded_at=after(self.t,day))
        hist=ChannelHistory(self.c,self.now)
        self.assertEqual(hist.outcomes(dict(hist.placements.iloc[0]))['new_users'],1)
        self.assertEqual(hist.outcomes(dict(hist.placements.iloc[1]))['new_users'],0)
        self.assertEqual(hist.overlap(['a','b'])[0]['jaccard'],1)
    def test_known_old_user_not_new_acquisition(self):
        self.add('placements',placement_id='a',channel_id='a',published_at=after(self.t,10),recorded_at=after(self.t,10),advertised_course_id='c')
        self.add('acquisition_events',event_id='a1',user_id=1,source_channel_id='a',placement_id='a',mechanism='bot_start',occurred_at=after(self.t,11),recorded_at=after(self.t,11))
        hist=ChannelHistory(self.c,self.now)
        self.assertEqual(hist.outcomes(dict(hist.placements.iloc[0]))['new_users'],0)


class MathAndContracts(unittest.TestCase):
    def test_decay_semantics(self):
        d=Decay(half_life=7)
        np.testing.assert_allclose(d.weights([0,7,14]),[1,.5,.25])
        self.assertEqual(d.weights([-1])[0],0)
        w=Decay('weibull_delayed',5,shape=3).weights(np.arange(20))
        self.assertGreater(np.argmax(w),0)
        self.assertLess(d.saturate(10)-d.saturate(9),d.saturate(2)-d.saturate(1))
    def test_invalid_plan_rejected(self):
        for p in [{'discount_pct':100},{'discount_pct':-1},{'contacts':[{'day':15,'kind':'message'}]},{'contacts':[{'day':2,'kind':'invented_read'}]}]:
            with self.assertRaises(ValueError):Plan.parse(p,14)
    def test_temporal_embargo(self):
        t=pd.date_range('2025-01-01',periods=100,freq='7D',tz='UTC')
        meta=pd.DataFrame({'time':[stamp(x) for x in t],'end':[after(x,14) for x in t]})
        folds,fit,cal,test,_=time_splits(meta)
        for tr,va in folds+[(fit,cal),(cal,test)]:
            self.assertLessEqual(meta.iloc[tr].end.max(),meta.iloc[va].time.min())
            self.assertFalse(set(tr)&set(va))
    def test_portfolio_bounds_and_unknown_overlap(self):
        q=[{'channel_id':'a','new_users':100},{'channel_id':'b','new_users':100}]
        r=portfolio_estimate(q,[{'channel_a':'a','channel_b':'b','jaccard':1}])
        self.assertEqual(r['new_users_pairwise_proxy'],100)
        r=portfolio_estimate(q,[])
        self.assertIsNone(r['new_users_pairwise_proxy']);self.assertEqual(r['union_bounds'],[100,200])


if __name__=='__main__':unittest.main()
