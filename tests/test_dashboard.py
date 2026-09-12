"""Economic consistency and redaction at the shared dashboard boundary."""
import tempfile
import unittest
from pathlib import Path

from app import ml_status,public_report
from analytics import report
from store import Store
from test_platform import catalog,at,T


class DashboardBoundaryTests(unittest.TestCase):
    def test_public_report_redacts_source_identifiers_but_preserves_money(self):
        original={'deals':[{'user_id':123456789,'order_id':'alice@example.org','net_minor':1234,
            'payments':[{'payment_id':'alice-payment','order_id':'alice@example.org','email':'alice@example.org'}],
            'touches':[{'event_id':'tg:123456789:1','source_token':'private-invite','chat_id':123456789}],
            'lead_ids':['private-lead']}], 'errors':{'imports':[{'filename':'alice-email.json','error':'alice@example.org'}]}}
        cleaned=public_report(original,'secret')
        data=str(cleaned)
        self.assertNotIn('123456789',data);self.assertNotIn('alice',data);self.assertNotIn('private-',data)
        self.assertEqual(cleaned['deals'][0]['net_minor'],1234)
        self.assertEqual(cleaned['deals'][0]['order_id'],cleaned['deals'][0]['payments'][0]['order_id'])
        self.assertEqual(original['deals'][0]['user_id'],123456789)
        self.assertNotEqual(cleaned,public_report(original,'another-secret'))

    def test_missing_ml_keeps_core_available_and_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'db.sqlite3'
            with Store(db):pass
            value=ml_status(db,Path(tmp)/'no-models')
            self.assertFalse(value['available']);self.assertEqual(value['status'],'Реализовано частично')
            self.assertTrue(value['reason'])

    def test_discount_not_charged_twice_and_incrementality_applies_to_revenue(self):
        with tempfile.TemporaryDirectory() as tmp,Store(Path(tmp)/'db.sqlite3') as s:
            s.load(catalog())
            s.load(dict(users=[dict(user_id=1,first_seen_at=at(2),recorded_at=at(2))],
                acquisition_events=[dict(event_id='entry',user_id=1,source_channel_id='ad',placement_id='p',mechanism='bot_start',occurred_at=at(2),recorded_at=at(2))],
                promotions=[dict(promotion_id='promo',title='20% скидка',kind='discount',created_at=T,recorded_at=T)],
                promotion_versions=[dict(version_id='pv',promotion_id='promo',effective_at=T,recorded_at=T,starts_at=at(3),ends_at=at(8),discount_type='percent',discount_value=20,terms='20%')],
                promotion_courses=[dict(link_id='pc',version_id='pv',course_id='course')],
                placement_promotions=[dict(placement_id='p',promotion_id='promo',recorded_at=T)],
                orders=[dict(order_id='order',user_id=1,ordered_at=at(5),recorded_at=at(5),total_minor=8000)],
                order_items=[dict(item_id='item',order_id='order',course_id='course',line_total_minor=8000)],
                order_promotions=[dict(order_id='order',version_id='pv',evidence='promo_code',recorded_at=at(5))],
                payments=[dict(payment_id='pay',order_id='order',kind='payment',amount_minor=8000,occurred_at=at(5),recorded_at=at(5))],
                placement_cost_events=[dict(cost_event_id='cost',placement_id='p',kind='expense',amount_minor=2000,occurred_at=T,recorded_at=T)],
                collection_periods=[dict(period_id='ad-costs',domain='ad_costs',starts_at=T,ends_at='2026-06-01',recorded_at='2026-06-01')],
                incrementality_evidence=[dict(evidence_id='inc',channel_id='ad',method='calibration',k=.5,starts_at=T,ends_at='2026-06-01',recorded_at=T,description='Допущение: 50% приписанной выручки дополнительно')]))
            r=report(s.db,'2026-05-01','2026-06-01')
            self.assertEqual(r['overview']['net_minor'],8000)
            self.assertEqual(r['overview']['discount_minor'],2000)
            self.assertEqual(r['overview']['cost_minor'],2000)
            self.assertEqual(r['overview']['romi_clean'],3)
            placement=next(x for x in r['placements'] if x['id']=='p')
            self.assertEqual(placement['cost_minor'],2000)
            self.assertEqual(placement['romi_clean'],3)
            self.assertEqual(placement['romi_inc'],1)  # (0.5 * 8000 - 2000) / 2000
            self.assertEqual(next(x for x in r['events'] if x['id']=='promo')['cost_minor'],2000)


if __name__=='__main__':unittest.main()
