import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from collector import Collector
from config import Config
from runner import flush_outbox, offset_for, process_lock
from store import Store
from telegram_api import APIError
from test_platform import catalog, at, T


class FakeAPI:
    def __init__(self, error=None):
        self.calls=[]
        self.error=error

    def call(self, method, **payload):
        self.calls.append((method,payload))
        if self.error:
            raise self.error
        return True


class BotReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'bot.sqlite3'
        self.s=Store(self.path)
        self.s.load(catalog())
        self.cfg=Config(db=self.path,channels=frozenset({-1001,-1002}))
        self.bot=Collector(self.s,self.cfg,700)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def start(self, uid, text='/start token', update=None):
        return self.bot.ingest(dict(update_id=update or uid,message=dict(
            message_id=uid,date=int(datetime.fromisoformat(at(2).replace('Z','+00:00')).timestamp()),
            chat=dict(id=uid,type='private'),**{'from':dict(id=uid,is_bot=False)},text=text)),received_at=at(2))

    def join(self, link, chat=-1001):
        return self.bot.ingest(dict(update_id=30,chat_member=dict(
            chat=dict(id=chat),date=int(datetime.fromisoformat(at(3).replace('Z','+00:00')).timestamp()),
            **{'from':dict(id=99)},old_chat_member=dict(status='left',user=dict(id=1)),
            new_chat_member=dict(status='member',user=dict(id=1)),invite_link=dict(invite_link=link))),received_at=at(3))

    def test_invite_cannot_attribute_other_destination_channel(self):
        self.s.load({'channels':[dict(channel_id='second',title='Second',kind='own_course',created_at=T,recorded_at=T)],
                     'telegram_channels':[dict(channel_id='second',chat_id=-1002,recorded_at=T)]})
        self.assertEqual(self.join('https://t.me/+demo',chat=-1002),'processed')
        row=self.s.db.execute('SELECT * FROM acquisition_events').fetchone()
        self.assertIsNone(row['source_channel_id'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM membership_events').fetchone()[0],1)

    def test_early_invite_preserves_join_but_does_not_attribute_future_ad(self):
        self.s.load({'placements':[dict(placement_id='future',channel_id='ad',published_at=at(4),recorded_at=T)],
                     'tracking_links':[dict(link_id='early',source_token='https://t.me/+early',source_channel_id='ad',
                         placement_id='future',destination_channel_id='main',mechanism='invite_link',created_at=T,recorded_at=T)]})
        self.assertEqual(self.join('https://t.me/+early'),'processed')
        self.assertIsNone(self.s.db.execute('SELECT source_channel_id FROM acquisition_events').fetchone()[0])
        self.assertEqual(self.s.db.execute('SELECT status FROM membership_events').fetchone()[0],'joined')
        self.assertEqual(self.s.db.execute('SELECT reason FROM acquisition_notes').fetchone()[0],'before_placement_publication')

    def test_source_link_without_paid_placement_is_supported(self):
        self.s.load({'tracking_links':[dict(link_id='source',source_token='source_token',source_channel_id='ad',
                                             mechanism='bot_start',created_at=T,recorded_at=T)]})
        self.assertEqual(self.start(1,'/start source_token'),'processed')
        row=self.s.db.execute('SELECT * FROM acquisition_events').fetchone()
        self.assertEqual(row['source_channel_id'],'ad')
        self.assertIsNone(row['placement_id'])

    def test_outbox_rate_limit_retries_without_repeating_business_events(self):
        self.start(1);self.start(2)
        api=FakeAPI(APIError(429,60))
        flush_outbox(self.s,api,700)
        self.assertEqual(len(api.calls),1)
        flush_outbox(self.s,api,700)
        self.assertEqual(len(api.calls),1)
        with self.s.db:self.s.db.execute('UPDATE bot_outbox SET retry_at=0')
        api.error=None
        flush_outbox(self.s,api,700)
        flush_outbox(self.s,api,700)
        self.assertEqual(len(api.calls),3)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM acquisition_events').fetchone()[0],2)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM bot_outbox WHERE status='sent'").fetchone()[0],2)

    def test_outbox_permanent_failure_is_recorded(self):
        self.start(1)
        api=FakeAPI(APIError(403))
        flush_outbox(self.s,api,700);flush_outbox(self.s,api,700)
        self.assertEqual(len(api.calls),1)
        self.assertEqual(self.s.db.execute('SELECT status FROM bot_outbox').fetchone()[0],'failed')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM acquisition_events').fetchone()[0],1)

    def test_import_conflict_quarantines_entire_file(self):
        p=Path(self.tmp.name)/'bad.json'
        p.write_text(json.dumps({'users':[dict(user_id=1,first_seen_at=T,recorded_at=T)],
            'payments':[dict(payment_id='absent',order_id='missing',kind='payment',amount_minor=100,occurred_at=at(2),recorded_at=at(2))]}))
        with self.assertRaises(ValueError):self.s.import_file(p)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM users').fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT status FROM import_runs').fetchone()[0],'failed')

    def test_dataset_binding_and_local_process_lock(self):
        with self.assertRaises(ValueError):Collector(self.s,Config(db=self.path,dataset='other'),701)
        with process_lock(self.path):
            with self.assertRaises(ValueError):
                with process_lock(self.path):pass

    def test_stale_polling_offset_is_dropped(self):
        with self.s.db:self.s.db.execute("UPDATE bot_runtime SET next_offset=555,last_update_at='2020-01-01T00:00:00Z'")
        self.assertIsNone(offset_for(self.s,700))


if __name__=='__main__':
    unittest.main()
