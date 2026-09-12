"""Exercise real HTTP import/read routes in an isolated copy, never production data."""
import json
import sys
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'http.sqlite3'
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from demo import generate
    generate(path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'app.py','--db',str(path),'--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    url=f'http://127.0.0.1:{port}'
    def get(route):
        with urllib.request.urlopen(url+route,timeout=5) as r:return json.load(r)
    def post(payload,token):
        req=urllib.request.Request(url+'/api/import',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':token,'Origin':url},method='POST')
        with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
    try:
        for _ in range(30):
            try:meta=get('/api/meta');break
            except OSError:time.sleep(.05)
        else:raise RuntimeError('HTTP server did not start')
        route='/api/report?start=2026-05-01&end=2026-09-11'
        before=get(route)['overview']['net_minor']
        at='2026-09-05T12:00:00Z'
        payload=dict(users=[dict(user_id=999123,first_seen_at=at,recorded_at=at)],
           acquisition_events=[dict(event_id='http:entry',user_id=999123,source_channel_id='career',placement_id='p01',mechanism='bot_start',source_token='p01',occurred_at=at,recorded_at=at)],
           orders=[dict(order_id='http:order',user_id=999123,ordered_at=at,recorded_at=at,total_minor=10000)],
           order_items=[dict(item_id='http:item',order_id='http:order',course_id='ml-start',line_total_minor=10000)],
           payments=[dict(payment_id='http:pay',order_id='http:order',kind='payment',amount_minor=10000,occurred_at=at,recorded_at=at)])
        assert post(payload,meta['csrf'])['inserted']==5
        assert get(route)['overview']['net_minor']==before+10000
        assert post(payload,meta['csrf'])['duplicates']==5
        assert get(route)['overview']['net_minor']==before+10000
        try:post(payload,'bad-token');raise AssertionError('CSRF accepted')
        except urllib.error.HTTPError as e:assert e.code==403
        invalid={'payments':[dict(payment_id='bad',order_id='http:order',kind='refund',amount_minor=10001,occurred_at=at,recorded_at=at)]}
        try:post(invalid,meta['csrf']);raise AssertionError('Excess refund accepted')
        except urllib.error.HTTPError as e:assert e.code==400
        assert get(route)['overview']['net_minor']==before+10000
        assert get(route)['quality']['failed_imports']>=1
        print('HTTP read/import/refresh, duplicate, CSRF and rollback: OK')
    finally:
        process.terminate();process.communicate(timeout=5)
