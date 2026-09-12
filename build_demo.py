"""Create one synthetic database for collection, dashboards and ML.

Existing files are never replaced. Training is a separate explicit CLI step.
"""
import argparse
from pathlib import Path


def enrich(path):
    from store import Store
    from postupashki_data import utc
    with Store(path) as s:
        rows = s.db.execute("SELECT value FROM ml_meta WHERE key='provenance'").fetchone()
        if not rows or rows[0] != 'synthetic':
            raise ValueError('Дополнение предназначено только для синтетической БД')
        with s.db:
            s.db.execute("UPDATE app_meta SET value='synthetic' WHERE key='provenance'")
        payload = {'tracking_links': [], 'placement_details': [], 'leads': [],
                   'order_leads': [], 'telegram_channels': []}
        payload['telegram_channels'].append(dict(channel_id='main',chat_id=-100900001,recorded_at=utc('2025-02-01')))
        for p in s.db.execute('SELECT * FROM placements'):
            pid=p['placement_id']
            payload['tracking_links'].append(dict(link_id='demo:start:'+pid,source_token=pid,
                source_channel_id=p['channel_id'],placement_id=pid,mechanism='bot_start',
                created_at=p['recorded_at'],recorded_at=p['recorded_at']))
            payload['placement_details'].append(dict(placement_id=pid,activity_type='ad',recorded_at=p['recorded_at']))
        # Synthetic leads complete the operational demonstration. They are not
        # model inputs, and their timing is an explicit fixture, not reconstructed data.
        for o in s.db.execute('SELECT * FROM orders'):
            lead='demo:lead:'+o['order_id']
            payload['leads'].append(dict(lead_id=lead,user_id=o['user_id'],interest='Синтетическая заявка',
                created_at=o['ordered_at'],recorded_at=o['recorded_at']))
            payload['order_leads'].append(dict(order_id=o['order_id'],lead_id=lead,recorded_at=o['recorded_at']))
        s.load(payload)
        s.validate()
        s.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default='data/demo.sqlite3')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--users-per-wave',type=int,default=60)
    p.add_argument('--enrich-only',action='store_true',help='Add operational fixtures to an existing synthetic ML database')
    a=p.parse_args()
    if not a.enrich_only:
        from postupashki_ml.synthetic import generate
        generate(a.db,seed=a.seed,users_per_wave=a.users_per_wave,rounds=12)
    enrich(Path(a.db))
    print('Синтетическая общая БД готова:',a.db)


if __name__=='__main__':
    main()
