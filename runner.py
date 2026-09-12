"""Long polling with an inbox, persistent cursor, outbox and one local writer."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import os
import signal
import threading
import time
from collector import Collector, now
from store import DataError, Store
from telegram_api import APIError, TelegramAPI

log = logging.getLogger(__name__)
ALLOWED_UPDATES = ['message','callback_query','chat_member','chat_join_request','my_chat_member']


@contextmanager
def process_lock(path):
    path = Path(str(path)+'.collector.lock')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0,2)
        if f.tell()==0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise DataError('Another collector is already using this database') from None
        try:
            yield
        finally:
            if os.name=='nt':
                f.seek(0)
                msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def flush_outbox(store, api, bot_id):
    rows = store.db.execute("SELECT * FROM bot_outbox WHERE bot_id=? AND status='pending' AND retry_at<=? ORDER BY update_id,seq LIMIT 100",(bot_id,time.time())).fetchall()
    for row in rows:
        rate_limited = False
        attempts = row['attempts']+1
        status,error,retry_at = 'sent',None,0
        try:
            api.call(row['method'],**json.loads(row['payload_json']))
        except APIError as e:
            rate_limited = e.code == 429
            status = 'failed' if e.code in (400,401,403,404) or attempts>=8 else 'pending'
            error = str(e)
            retry_at = time.time()+max(e.retry_after,min(300,2**attempts))
            log.warning('Reply %s/%s/%s: %s',bot_id,row['update_id'],row['seq'],status)
        with store.db:
            store.db.execute('UPDATE bot_outbox SET status=?,attempts=?,retry_at=?,error=? WHERE bot_id=? AND update_id=? AND seq=?',
                (status,attempts,retry_at,error,bot_id,row['update_id'],row['seq']))
            if rate_limited:
                store.db.execute("UPDATE bot_outbox SET retry_at=max(retry_at,?) WHERE bot_id=? AND status='pending'",(retry_at,bot_id))
        if rate_limited:
            break


def snapshot(store, api, config):
    for chat in sorted(config.channels):
        try:
            n = api.call('getChatMemberCount',chat_id=chat)
            with store.db:
                from postupashki_data import utc
                at=utc(); cid=store.channel(chat)['channel_id']
                store.add('channel_snapshots',snapshot_id=f'tg:{chat}:{at}',channel_id=cid,
                          observed_at=at,recorded_at=at,subscribers=n,source='public_counter')
        except APIError as e:
            log.warning('Could not read subscriber count for %s: %s',chat,e)


def offset_for(store, bot_id):
    row = store.db.execute('SELECT * FROM bot_runtime WHERE bot_id=?',(bot_id,)).fetchone()
    if row and row['last_update_at']:
        age = datetime.now(timezone.utc)-datetime.fromisoformat(row['last_update_at'])
        # Telegram may randomize the next update ID after a week without updates.
        if age.total_seconds() < 6*86400:
            return row['next_offset']
    return None


def run(config):
    config.require_token()
    api = TelegramAPI(config.token)
    me = api.call('getMe')
    if api.call('getWebhookInfo').get('url'):
        raise DataError('A webhook is already configured. Remove it explicitly before using long polling')
    for chat in sorted(config.channels):
        rights = api.call('getChatMember',chat_id=chat,user_id=me['id'])
        if rights['status'] not in ('administrator','creator'):
            raise DataError(f'Bot must be an administrator in channel {chat}')
    stop = threading.Event()
    def halt(*_):
        stop.set()
    for sig in (signal.SIGINT,signal.SIGTERM):
        signal.signal(sig,halt)
    with process_lock(str(config.db)+f".{me['id']}"), Store(config.db) as store:
        bot = Collector(store,config,me['id'])
        pending = store.db.execute("SELECT payload_json FROM bot_updates WHERE bot_id=? AND status='pending' ORDER BY received_at",(me['id'],)).fetchall()
        for row in pending:
            bot.ingest(json.loads(row[0]))
        log.info('Bot @%s started; monitored channels: %s',me['username'],len(config.channels))
        last_snapshot = time.monotonic()-3601
        while not stop.is_set():
            flush_outbox(store,api,me['id'])
            if time.monotonic()-last_snapshot > 3600:
                snapshot(store,api,config)
                last_snapshot = time.monotonic()
            payload = dict(timeout=25,limit=100,allowed_updates=ALLOWED_UPDATES)
            offset = offset_for(store,me['id'])
            if offset is not None:
                payload['offset'] = offset
            try:
                updates = api.call('getUpdates',**payload)
            except APIError as e:
                if e.code in (401,404,409):
                    raise DataError('Telegram rejected polling: check token, webhook and other running copies') from None
                log.warning('%s',e)
                stop.wait(min(30,max(2,e.retry_after)))
                continue
            for update in updates:
                bot.ingest(update)
                # Acknowledge only once the full update is durably saved, including failures.
                with store.db:
                    store.db.execute('UPDATE bot_runtime SET next_offset=?,last_update_at=? WHERE bot_id=?',
                                     (update['update_id']+1,now(),me['id']))
                flush_outbox(store,api,me['id'])
        log.info('Collector stopped. Events and polling position are saved.')
