"""Local dashboard: direct SQLite reads, atomic uploads; no third-party dependencies."""
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import argparse
import json
import secrets
import sqlite3
import hashlib
import hmac
from config import Config
from store import Store
from analytics import report
from postupashki_data import utc

ROOT=Path(__file__).parent/'dashboard'


def public_report(data,secret):
    """Keep drill-downs useful without exporting source customer identifiers."""
    private_ids={'user_id':'Клиент','order_id':'Заказ','payment_id':'Платёж',
                 'item_id':'Позиция','event_id':'Событие','lead_id':'Заявка'}
    omit={'source_token','telegram_id','chat_id','username','phone','email','raw_json','payload_json'}
    def alias(key,value):
        digest=hmac.new(secret.encode(),f'{key}:{value}'.encode(),hashlib.sha256).hexdigest()[:10]
        return private_ids[key]+' '+digest
    def clean(value,key=None):
        if key in private_ids and value is not None:return alias(key,value)
        if key=='lead_ids':return [alias('lead_id',v) for v in value]
        if key=='error' and value:return 'Подробности сохранены в локальном журнале.'
        if key=='filename':return 'Импорт JSON'
        if isinstance(value,dict):return {k:clean(v,k) for k,v in value.items() if k not in omit}
        if isinstance(value,list):return [clean(v) for v in value]
        return value
    return clean(data)


def ml_status(path,model_dir):
    try:
        from ml_bridge import status
        return status(path,model_dir)
    except (ImportError,OSError,ValueError,sqlite3.Error) as e:
        return dict(status='Реализовано частично',available=False,
                    reason='ML-модуль или сохранённые модели пока недоступны. См. ml/README.md.',
                    provenance=metadata(path)['provenance'],models=[])


def read_connection(path):
    con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=30)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con

def metadata(path):
    from contextlib import closing
    with closing(read_connection(path)) as con:
        row=con.execute('SELECT MIN(first_seen_at) FROM users').fetchone()
        return dict(start=(row[0] or utc())[:10],end=datetime.now(timezone.utc).date().isoformat(),
                    provenance=dict(con.execute('SELECT key,value FROM app_meta')).get('provenance','real'))


def export(path,dest,start=None,end=None,model_dir='ml/models'):
    meta=metadata(path);start=start or meta['start'];end=end or meta['end']
    with Store(path) as s:r=public_report(report(s.db,start,utc(datetime.fromisoformat(end).replace(tzinfo=timezone.utc)+timedelta(days=1))),secrets.token_urlsafe(32))
    html=(ROOT/'index.html').read_text(encoding='utf-8')
    html=html.replace('<link rel="stylesheet" href="/style.css">','<style>'+ (ROOT/'style.css').read_text(encoding='utf-8')+'</style>')
    boot=json.dumps(dict(report=r,meta=meta,ml=ml_status(path,model_dir)),ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    html=html.replace('<script src="/app.js" defer></script>','<script>window.SNAPSHOT='+boot+';</script><script>'+ (ROOT/'app.js').read_text(encoding='utf-8')+'</script>')
    Path(dest).parent.mkdir(parents=True,exist_ok=True);Path(dest).write_text(html,encoding='utf-8')


def serve(path,port=8501,model_dir='ml/models'):
    with Store(path):pass
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,code,data,mime='application/json; charset=utf-8'):
            content=json.dumps(data,ensure_ascii=False,allow_nan=False).encode() if isinstance(data,(dict,list)) else data
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(content)
        def safe_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')
        def do_GET(self):
            if not self.safe_host():return self.reply(403,{'error':'Недопустимый адрес сервера'})
            u=urlparse(self.path);q=parse_qs(u.query)
            try:
                if u.path=='/api/meta':return self.reply(200,dict(metadata(path),csrf=token))
                if u.path=='/api/ml/status':return self.reply(200,ml_status(path,model_dir))
                if u.path=='/api/report':
                    meta=metadata(path);start=q.get('start',[meta['start']])[0];end=q.get('end',[meta['end']])[0]
                    end_at=utc(datetime.fromisoformat(end).replace(tzinfo=timezone.utc)+timedelta(days=1))
                    from contextlib import closing
                    with closing(read_connection(path)) as con:r=report(con,start,end_at,q.get('method',['last_touch'])[0],int(q.get('lookback',['90'])[0]))
                    return self.reply(200,public_report(r,token))
                name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}.get(u.path)
                if not name:return self.reply(404,{'error':'Не найдено'})
                mime={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name]
                return self.reply(200,(ROOT/name).read_bytes(),mime)
            except (ValueError,sqlite3.Error,OSError) as e:self.reply(400,{'error':str(e)})
        def do_POST(self):
            if not self.safe_host() or self.headers.get('X-CSRF-Token')!=token:return self.reply(403,{'error':'Обновите страницу перед загрузкой'})
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://localhost:{port}',f'http://127.0.0.1:{port}'):return self.reply(403,{'error':'Недопустимый источник запроса'})
            if self.path not in ('/api/import','/api/ml/customers','/api/ml/channels'):return self.reply(404,{'error':'Не найдено'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=5*1024*1024:return self.reply(413,{'error':'Ограничение JSON: 5 МБ'})
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict):raise ValueError('Тело запроса должно быть JSON-объектом.')
                if self.path.startswith('/api/ml/'):
                    if not ml_status(path,model_dir).get('available'):
                        return self.reply(503,{'error':ml_status(path,model_dir).get('reason','Сначала обучите ML-модели командой из ml/README.md.')})
                    from ml_bridge import predict_customers,predict_channels
                    predictor=predict_customers if self.path.endswith('/customers') else predict_channels
                    result=predictor(path,model_dir,payload)
                    return self.reply(200,result)
                with Store(path) as s:
                    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                    try:result=s.load(payload)
                    except (ValueError,sqlite3.Error) as e:
                        with s.db:s.db.execute('INSERT OR REPLACE INTO import_runs VALUES(?,?,?,?,?,?)',('dashboard',digest,'dashboard-upload.json',utc(),'failed',str(e)[:1000]))
                        raise
                    with s.db:s.db.execute('INSERT OR REPLACE INTO import_runs VALUES(?,?,?,?,?,NULL)',('dashboard',digest,'dashboard-upload.json',utc(),'succeeded'))
                self.reply(200,result)
            except ImportError:self.reply(503,{'error':'Для прогноза установите ML-зависимости: python -m pip install -r ml/requirements.txt'})
            except (ValueError,TypeError,KeyError,sqlite3.Error,OSError) as e:self.reply(400,{'error':str(e)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Дашборд: http://127.0.0.1:{port} · База: {path}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description='Дашборд Поступашек')
    p.add_argument('--db');p.add_argument('--port',type=int,default=8501)
    p.add_argument('--models',default='ml/models',help='Каталог моделей; обучение запускается отдельно через CLI')
    p.add_argument('--export');p.add_argument('--start');p.add_argument('--end')
    args=p.parse_args();cfg=Config.read(db=args.db)
    if args.export:export(cfg.db,args.export,args.start,args.end,args.models)
    else:serve(cfg.db,args.port,args.models)
