"""Reproduce the collector -> shared database -> analytics path without any network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from analytics import report
from store import Store


ROOT=Path(__file__).resolve().parents[1]
T='2026-05-01T00:00:00Z'


def main():
    commands=[]
    with tempfile.TemporaryDirectory(prefix='postupashki-bot-') as folder:
        work=Path(folder)
        path=work/'synthetic.sqlite3'
        envfile=work/'settings.env'
        envfile.write_text('BOT_TOKEN=\nDATASET_ID=bot_smoke\nMANAGER_USERNAME=manager_demo\nADMIN_IDS=99\nCHANNEL_IDS=-1001\n',encoding='utf-8')
        env={k:v for k,v in os.environ.items() if k not in ('BOT_TOKEN','DB_PATH','DATASET_ID','MANAGER_USERNAME','ADMIN_IDS','CHANNEL_IDS')}

        def run(*args,expected=0):
            result=subprocess.run([sys.executable,'bot.py','--db',str(path),'--env',str(envfile),*args],cwd=ROOT,env=env,capture_output=True,text=True)
            if result.returncode!=expected:
                raise RuntimeError(f'{args}: exit={result.returncode}; {result.stdout} {result.stderr}')
            commands.append({'command':'bot.py '+' '.join(Path(a).name if str(work) in a else a for a in args),
                             'exit_code':result.returncode})
            return result.stdout

        def file(name,value):
            p=work/name
            p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
            return str(p)

        run('init')
        with Store(path) as s:
            with s.db:s.db.execute("UPDATE app_meta SET value='synthetic' WHERE key='provenance'")
        catalog={'topics':[dict(topic_id='ml',title='ДЕМО: ML')],
            'channels':[dict(channel_id='main',title='ДЕМО: наш канал',kind='own_main',created_at=T,recorded_at=T),
                        dict(channel_id='ad',title='ДЕМО: источник',kind='external',created_at=T,recorded_at=T)],
            'courses':[dict(course_id='course',topic_id='ml',title='ДЕМО: курс',created_at=T,recorded_at=T)],
            'placements':[dict(placement_id='p',channel_id='ad',published_at=T,recorded_at=T)],
            'tracking_links':[dict(link_id='l',source_token='offline_token',source_channel_id='ad',placement_id='p',mechanism='bot_start',created_at=T,recorded_at=T)]}
        run('import',file('catalog.json',catalog))
        run('link','offline_token','--bot','example_bot')
        updates=[dict(update_id=1,message=dict(message_id=1,date=1777723200,chat=dict(id=10001,type='private'),
                     **{'from':dict(id=10001,is_bot=False)},text='/start offline_token')),
                 dict(update_id=2,message=dict(message_id=2,date=1777809600,chat=dict(id=10001,type='private'),
                     **{'from':dict(id=10001,is_bot=False)},text='/manager'))]
        updates_path=file('updates.json',updates)
        run('replay',updates_path,'--bot-id','700')
        run('replay',updates_path,'--bot-id','700')
        joined=dict(update_id=3,chat_member=dict(chat=dict(id=-1001),date=1777809600,**{'from':dict(id=99)},
            old_chat_member=dict(status='left',user=dict(id=10001)),new_chat_member=dict(status='member',user=dict(id=10001))))
        run('replay',file('join.json',joined),'--bot-id','700',expected=1)
        run('bind-channel','main','-1001')
        run('retry-update','700','3')
        payment={'orders':[dict(order_id='order',user_id=10001,ordered_at='2026-05-05T12:00:00Z',total_minor=1000000)],
            'order_items':[dict(item_id='item',order_id='order',course_id='course',quantity=1,line_total_minor=1000000)],
            'payments':[dict(payment_id='first',order_id='order',kind='payment',amount_minor=400000,occurred_at='2026-05-05T12:00:00Z'),
                        dict(payment_id='second',order_id='order',kind='payment',amount_minor=600000,occurred_at='2026-05-10T12:00:00Z'),
                        dict(payment_id='refund',order_id='order',kind='refund',amount_minor=300000,occurred_at='2026-05-12T12:00:00Z')]}
        p=file('payment.json',payment)
        run('import',p);run('import',p)
        run('features')
        run('backup',str(work/'backup.sqlite3'))
        run('retry-replies','700')
        run('errors')
        # This must fail before contacting Telegram, because there is no token.
        run('run',expected=1)
        with Store(path) as s:
            r=report(s.db,'2026-05-01','2026-06-01')
            counts={table:s.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                    for table in ('users','acquisition_events','membership_events','leads','orders','payments','bot_updates','user_features')}
            assert counts==dict(users=1,acquisition_events=2,membership_events=1,leads=1,orders=1,payments=3,bot_updates=3,user_features=1),counts
            assert r['overview']['net_minor']==700000,r['overview']
            assert r['overview']['clean']==700000,r['overview']
            assert s.db.execute("SELECT count(*) FROM bot_updates WHERE status='failed'").fetchone()[0]==0
            assert s.db.execute("SELECT count(*) FROM bot_outbox WHERE status='sent'").fetchone()[0]==0
        result={'status':'Реализовано частично','scope':'Локальный офлайн сценарий на синтетических событиях; live Telegram/CRM не проверены',
                'network_calls':0,'counts':counts,'net_minor':700000,'clean_attributed_minor':700000,
                'commands':commands,'temporary_database_removed':True}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    sys.exit(main())
