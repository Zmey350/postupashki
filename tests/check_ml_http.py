"""Exercise trained ML and operational HTTP against an isolated copy of one DB."""
import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1]
database=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/'data/demo.sqlite3'
models=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root/'ml/models'
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'http.sqlite3'
    with sqlite3.connect(database) as src,sqlite3.connect(path) as dst:src.backup(dst)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    proc=subprocess.Popen([sys.executable,'app.py','--db',str(path),'--models',str(models),'--port',str(port)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    url=f'http://127.0.0.1:{port}'
    def request(route,payload=None,token=None):
        headers={'Content-Type':'application/json','Origin':url}
        if token:headers['X-CSRF-Token']=token
        req=urllib.request.Request(url+route,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=90) as r:return json.load(r)
    try:
        for _ in range(100):
            try:meta=request('/api/meta');break
            except OSError:time.sleep(.05)
        else:raise RuntimeError('Server did not start')
        status=request('/api/ml/status');assert status['available'],status['reason']
        before=request('/api/report')['overview']
        payload=dict(course_id=status['course_id'],as_of=status['as_of'],plan=dict(discount_pct=20,discount_days=14,contacts=[dict(kind='message',day=0),dict(kind='message',day=7)]),required_roi=.3)
        customers=request('/api/ml/customers',payload,meta['csrf'])
        assert customers['eligible_users']>0 and customers['expected_buyers_current']>=0
        assert 'rows' not in customers and 'user_id' not in json.dumps(customers)
        assert customers['provenance']==status['provenance']==meta['provenance']
        assert customers['status']=='Реализовано частично'
        payload['channel_id']=status['channels'][0]['channel_id']
        channel=request('/api/ml/channels',payload,meta['csrf'])
        assert channel['maximum_price_minor'] is None
        payload['incrementality']=.7;payload['quoted_price_minor']=1000000
        priced=request('/api/ml/channels',payload,meta['csrf'])
        assert priced['maximum_price_minor']>=0
        assert priced['maximum_price_minor']<=priced['attributed_price_ceiling_minor']
        assert request('/api/report')['overview']==before,'Forecast changed operational facts'
        for route,token,expected in [('/api/ml/customers','wrong',403),('/api/ml/train',meta['csrf'],404)]:
            try:request(route,payload,token);raise AssertionError('Unexpected authorization')
            except urllib.error.HTTPError as e:assert e.code==expected
        print(json.dumps(dict(check='one database: operational report + customer scenario + channel quote + unchanged facts + no training/PII route',status='OK',provenance=status['provenance'],users=customers['eligible_users'],expected_current=customers['expected_buyers_current'],expected_scenario=customers['expected_buyers_scenario'],maximum_price_minor=priced['maximum_price_minor']),ensure_ascii=False))
    finally:proc.terminate();proc.communicate(timeout=10)
