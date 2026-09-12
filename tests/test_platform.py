import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from collector import Collector
from config import Config
from store import Store
from analytics import Analytics,report
from postupashki_data import utc,build_features

T='2026-05-01T00:00:00Z'
def at(day):return utc(f'2026-05-{day:02}T12:00:00Z')

def catalog():
    return dict(topics=[dict(topic_id='ml',title='ML')],
      channels=[dict(channel_id='main',title='Наш канал',kind='own_main',created_at=T,recorded_at=T),dict(channel_id='ad',title='Реклама',kind='external',created_at=T,recorded_at=T),dict(channel_id='ad2',title='Второй источник',kind='external',created_at=T,recorded_at=T)],
      telegram_channels=[dict(channel_id='main',chat_id=-1001,recorded_at=T)],
      courses=[dict(course_id='course',topic_id='ml',title='Курс',created_at=T,recorded_at=T)],
      course_versions=[dict(version_id='course:1',course_id='course',effective_at=T,recorded_at=T,sales_open_at=T,sales_close_at=utc('2026-12-01'),starts_at=utc('2026-10-01'),regular_price_minor=10000)],
      placements=[dict(placement_id='p',channel_id='ad',published_at=T,recorded_at=T,quoted_cost_minor=2000),dict(placement_id='p2',channel_id='ad2',published_at=T,recorded_at=T,quoted_cost_minor=2000)],
      tracking_links=[dict(link_id='link',source_token='token',source_channel_id='ad',placement_id='p',mechanism='bot_start',created_at=T,recorded_at=T),dict(link_id='invite',source_token='https://t.me/+demo',source_channel_id='ad',placement_id='p',destination_channel_id='main',mechanism='invite_link',created_at=T,recorded_at=T)],
      hackathons=[dict(hackathon_id='hack',topic_id='ml',title='Хакатон',starts_at=at(5),ends_at=at(7),recorded_at=T)])

class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite3';self.s=Store(self.path);self.s.load(catalog())
        self.cfg=Config(db=self.path,channels=frozenset({-1001}),manager='manager_demo',admins=frozenset({99}))
        self.bot=Collector(self.s,self.cfg,700)
    def tearDown(self):self.s.db.close();self.tmp.cleanup()
    def msg(self,text,day=2,uid=1,update=1):
        return dict(update_id=update,message=dict(message_id=update,date=int(datetime.fromisoformat(at(day).replace('Z','+00:00')).timestamp()),
               chat=dict(id=uid,type='private'),**{'from':dict(id=uid,is_bot=False)},text=text))
    def send(self,text,day=2,uid=1,update=1):return self.bot.ingest(self.msg(text,day,uid,update),received_at=at(day))
    def membership(self,old,new,day=3,update=3,invite=True):
        x=dict(chat=dict(id=-1001),date=int(datetime.fromisoformat(at(day).replace('Z','+00:00')).timestamp()),
              **{'from':dict(id=99)},old_chat_member=dict(status=old,user=dict(id=1)),new_chat_member=dict(status=new,user=dict(id=1)))
        if invite:x['invite_link']=dict(invite_link='https://t.me/+demo')
        return self.bot.ingest(dict(update_id=update,chat_member=x),received_at=at(day))
    def sale(self,day=5,uid=1,total=10000):
        return dict(orders=[dict(order_id='o',user_id=uid,ordered_at=at(day),recorded_at=at(day),total_minor=total)],
          order_items=[dict(item_id='o:1',order_id='o',course_id='course',line_total_minor=total)],
          payments=[dict(payment_id='pay',order_id='o',kind='payment',amount_minor=total,occurred_at=at(day),recorded_at=at(day))])
    def stats(self,method='last_touch'):return report(self.s.db,'2026-05-01','2026-06-01',method)

    def test_full_bot_to_database_to_dashboard_and_duplicate(self):
        self.assertEqual(self.send('/start token'),'processed');self.assertEqual(self.send('/start token'),'processed')
        self.assertEqual(self.send('/manager',day=3,update=2),'processed')
        payload=self.sale();self.s.load(payload);self.s.load(payload)
        r=self.stats();self.assertEqual(r['overview']['net_minor'],10000);self.assertEqual(r['overview']['clean'],10000)
        self.assertEqual(r['funnel']['clean']['counts'],[1,1,1,1])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM acquisition_events').fetchone()[0],1)
        self.assertEqual(len(r['deals']),1)

    def test_leave_never_attributed_and_actor_not_user(self):
        self.membership('left','member');self.membership('member','left',day=4,update=4)
        self.s.load(self.sale())
        r=self.stats();self.assertEqual(r['deals'][0]['clean'][0]['basis'],'invite_link')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM users WHERE user_id=99').fetchone()[0],0)
        self.assertEqual(sum(x['net'] for x in r['growth']),0)

    def test_manual_only_in_mixed_and_unknown_not_invented(self):
        self.send('/source token');self.s.load(self.sale())
        r=self.stats();self.assertEqual(r['overview']['clean'],0);self.assertEqual(r['overview']['mixed'],10000)
        self.assertEqual(r['quality']['mixed_only_pct'],1)
        self.send('/start',uid=2,update=20)
        row=self.s.db.execute('SELECT * FROM acquisition_events WHERE user_id=2').fetchone();self.assertIsNone(row['source_channel_id'])

    def test_refund_installments_and_time_window(self):
        self.send('/start token');p=self.sale();p['payments'][0]['amount_minor']=4000;self.s.load(p)
        self.s.load({'payments':[dict(payment_id='second',order_id='o',kind='payment',amount_minor=6000,occurred_at=at(10),recorded_at=at(10)),dict(payment_id='refund',order_id='o',kind='refund',amount_minor=3000,occurred_at=at(12),recorded_at=at(12))]})
        r=self.stats();self.assertEqual(r['overview']['net_minor'],7000);self.assertEqual(r['overview']['buyers'],1);self.assertEqual(r['overview']['orders'],1)
        only=report(self.s.db,'2026-05-12','2026-05-13');self.assertEqual(only['overview']['clean'],-3000)

    def test_invalid_payment_atomic_rollback_and_source_preserved(self):
        self.send('/start token');self.s.load(self.sale())
        with self.assertRaises(ValueError):self.s.load({'profile_events':[dict(event_id='new',user_id=1,field='education_stage',value='school',source='bot_form',occurred_at=at(8),recorded_at=at(8))], 'payments':[dict(payment_id='excess',order_id='o',kind='payment',amount_minor=1,occurred_at=at(8),recorded_at=at(8))]})
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM profile_events WHERE event_id='new'").fetchone()[0],0)
        self.assertEqual(self.stats()['overview']['net_minor'],10000)

    def test_event_funnel_requires_submission_and_admin(self):
        self.send('/register hack',day=3)
        self.send('/complete hack 1',day=4,uid=99,update=2)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM completion_events').fetchone()[0],0)
        self.send('/submit hack https://example.org/project',day=6,update=3)
        self.send('/complete hack 1',day=7,uid=99,update=4)
        self.send('/complete hack 1',day=7,uid=99,update=5)
        r=self.stats()['events'][0];self.assertEqual((r['registered'],r['active'],r['completed']),(1,1,1));self.assertIsNone(r['retained30'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM completion_events').fetchone()[0],1)

    def test_channel_binding_failure_can_retry_same_update(self):
        cfg=Config(db=self.path,channels=frozenset({-1002}));bot=Collector(self.s,cfg,800)
        u=dict(update_id=1,chat_member=dict(chat=dict(id=-1002),date=int(datetime.fromisoformat(at(3).replace('Z','+00:00')).timestamp()),**{'from':dict(id=99)},old_chat_member=dict(status='left',user=dict(id=5)),new_chat_member=dict(status='member',user=dict(id=5))))
        self.assertEqual(bot.ingest(u,received_at=at(3)),'failed')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM users WHERE user_id=5').fetchone()[0],0)
        self.s.load({'channels':[dict(channel_id='other',title='Другой',kind='own_course',created_at=T,recorded_at=T)],'telegram_channels':[dict(channel_id='other',chat_id=-1002,recorded_at=T)]})
        self.assertEqual(bot.ingest(u,retry=True),'processed')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM membership_events WHERE user_id=5').fetchone()[0],1)

    def test_conflicting_update_rejected_without_overwrite(self):
        self.send('/start token')
        with self.assertRaises(ValueError):self.send('/source token')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM manual_sources').fetchone()[0],0)

    def test_linear_conserves_money_and_no_post_order_leak(self):
        self.send('/start token');self.s.load({'tracked_clicks':[dict(event_id='click2',user_id=1,source_channel_id='ad2',placement_id='p2',occurred_at=at(3),recorded_at=at(3))]});self.s.load(self.sale())
        self.send('/source token',day=6,update=2)
        r=self.stats('linear');self.assertAlmostEqual(sum(x['clean_minor'] for x in r['sources']),10000)
        self.assertEqual(len(r['deals'][0]['mixed']),2)
        self.assertEqual(self.stats()['deals'][0]['clean'][0]['source_channel_id'],'ad2')
        self.assertEqual(self.stats('first_touch')['deals'][0]['clean'][0]['source_channel_id'],'ad')

    def test_calendar_fallback_conservative_and_explicit(self):
        self.send('/start')
        self.s.load({'promotions':[dict(promotion_id='promo',title='Скидка',kind='discount',created_at=T,recorded_at=T)],
           'promotion_versions':[dict(version_id='v',promotion_id='promo',effective_at=T,recorded_at=T,starts_at=at(4),ends_at=at(8),discount_type='percent',discount_value=20,terms='20%')],
           'promotion_courses':[dict(link_id='pc',version_id='v',course_id='course')],
           'placement_promotions':[dict(placement_id='p',promotion_id='promo',recorded_at=T)]})
        self.s.load(self.sale(total=8000));r=self.stats();self.assertEqual(r['overview']['clean'],0);self.assertEqual(r['overview']['mixed'],8000)
        self.assertEqual(r['deals'][0]['mixed'][0]['basis'],'calendar_price_match')
        self.s.load({'placement_promotions':[dict(placement_id='p2',promotion_id='promo',recorded_at=T)]})
        self.assertEqual(self.stats()['overview']['mixed'],0)

    def test_cohort_censoring_and_unknown_coverage(self):
        self.send('/start token');self.s.load(self.sale())
        c=self.stats()['cohorts'][0];self.assertIsNone(c['curve'][0]['clean_conversion']);self.assertEqual(c['curve'][0]['clean_buyers'],1)
        self.assertEqual(c['curve'][3]['mature'],0);self.assertIsNone(c['ltv_cac'])
        self.s.load({'collection_periods':[dict(period_id='com',domain='commerce',starts_at=T,ends_at=at(20),recorded_at=at(20))]})
        self.assertEqual(self.stats()['cohorts'][0]['curve'][0]['clean_conversion'],1)

    def test_earlier_bot_observation_moves_first_seen_only_back(self):
        self.send('/start token',day=4,update=4);self.send('/start token',day=2,update=2)
        self.assertEqual(self.s.db.execute('SELECT first_seen_at FROM users').fetchone()[0],at(2))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM acquisition_events').fetchone()[0],2)

    def test_offer_callback_cannot_record_for_other_user(self):
        self.send('/start token');self.s.load({'offers':[dict(offer_id='offer',user_id=1,course_id='course',regular_price_minor=10000,offered_price_minor=9000,status='sent',sent_at=at(3),valid_until=at(9),recorded_at=at(3))]})
        u=dict(update_id=4,callback_query=dict(id='cb',data='offer:offer:clicked',**{'from':dict(id=2)},message=dict(chat=dict(id=2,type='private'))))
        self.assertEqual(self.bot.ingest(u,received_at=at(4)),'processed')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM promotion_responses').fetchone()[0],0)

    def test_original_user_features_still_build_after_events(self):
        self.send('/start token');self.send('/profile education_stage university',day=3,update=2);self.send('/price course',day=3,update=3)
        self.assertEqual(build_features(self.s.db,utc('2026-06-01')),1)
        r=self.s.db.execute('SELECT * FROM user_features').fetchone();self.assertEqual(r['education_stage'],'university')

    def test_forged_forecast_known_at_rolls_back(self):
        row=dict(run_id='run',model='external',origin_at=at(4),created_at=at(4),training_end_at=at(3),horizon_days=7,backtest_n=0,known_campaigns_json=json.dumps([dict(id='p',kind='placement',known_at=at(2))]),notes='test')
        with self.assertRaises(ValueError):self.s.load({'forecast_runs':[row]})
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM forecast_runs').fetchone()[0],0)

    def test_old_mvp_is_rejected_not_destroyed(self):
        path=Path(self.tmp.name)/'old.db'
        with sqlite3.connect(path) as c:c.execute('CREATE TABLE users(dataset_id TEXT,user_key TEXT)');c.execute("INSERT INTO users VALUES('real','tg:1')")
        with self.assertRaises(ValueError):Store(path)
        with sqlite3.connect(path) as c:self.assertEqual(c.execute('SELECT user_key FROM users').fetchone()[0],'tg:1')

if __name__=='__main__':unittest.main()
