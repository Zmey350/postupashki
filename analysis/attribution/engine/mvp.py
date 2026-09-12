"""Local, dependency-free marketing measurement prototype. Python 3.10+."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from contextlib import AbstractContextManager
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT.parent / 'data'
from attribution_rules import MODELS, allocate


class DataError(ValueError):
    pass


def ident(x, field='id'):
    if not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', x):
        raise DataError(f'{field}: use 1-100 Latin letters, digits, _, ., :, -')
    return x


def cents(x, positive=True):
    if isinstance(x, bool) or x is None:
        raise DataError('A monetary amount is required')
    try:
        a = Decimal(''.join(str(x).split()).replace(',', '.'))
    except InvalidOperation as e:
        raise DataError('Invalid monetary amount') from e
    if not a.is_finite() or a < 0 or (positive and a == 0):
        raise DataError('Amount must be positive, or nonnegative where allowed')
    n = a * 100
    if n != n.to_integral_value() or n > 10**15:
        raise DataError('Amount must have at most two decimal places and be within range')
    return int(n)


def rub(n):
    return str(Decimal(n) / 100)


def time_value(clock, s):
    if not isinstance(s, str):
        raise DataError('Time must be an ISO-8601 string')
    try:
        t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError as e:
        raise DataError(f'Invalid ISO time: {s}') from e
    if clock == 'UTC':
        if t.tzinfo is None:
            raise DataError('UTC dataset requires an explicit UTC offset or Z')
        t = t.astimezone(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        if t.tzinfo is not None:
            raise DataError('local_unspecified dataset requires a local time without an offset')
        epoch = datetime(1970, 1, 1)
    d = t - epoch
    us = (d.days * 86400 + d.seconds) * 1000000 + d.microseconds
    return t.isoformat(timespec='microseconds'), us


class Store(AbstractContextManager):
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))

    def __exit__(self, kind, value, tb):
        if kind is None:
            self.db.commit()
        else:
            self.db.rollback()
        self.db.close()
        return False

    def get(self, table, ds, key):
        if table not in ('placements','touches','leads','orders','payments'):
            raise DataError('Unsupported table')
        r = self.db.execute(f'SELECT * FROM {table} WHERE dataset_id=? AND id=?', (ds,key)).fetchone()
        if r is None:
            raise DataError(f'{table}: unknown id {key} in dataset {ds}')
        return dict(r)

    def dataset(self, ds):
        r = self.db.execute('SELECT * FROM datasets WHERE id=?', (ds,)).fetchone()
        if r is None:
            raise DataError(f'Unknown dataset: {ds}')
        return dict(r)

    def existing(self, table, row, keys):
        # Table and column names come only from the fixed record implementations below.
        where = ' AND '.join(k+'=?' for k in keys)
        old = self.db.execute(f'SELECT * FROM {table} WHERE {where}', [row[k] for k in keys]).fetchone()
        if old is None:
            return False
        if dict(old) != row:
            raise DataError(f'Conflicting reuse of an existing {table} key')
        return True

    def insert(self, table, row, keys):
        if self.existing(table,row,keys):
            return False
        cols = ','.join(row)
        qs = ','.join('?' for _ in row)
        self.db.execute(f'INSERT INTO {table} ({cols}) VALUES ({qs})', list(row.values()))
        return True

    def ensure_user(self, ds, user):
        ident(user,'user_key')
        self.db.execute('INSERT OR IGNORE INTO users VALUES (?,?)', (ds,user))

    def stamp(self, ds, s):
        return time_value(self.dataset(ds)['clock'],s)

    def record(self, ds, e):
        if not isinstance(e,dict):
            raise DataError('Each record must be an object')
        kind = e.get('type')
        schemas = {
            'placement': {'type','id','campaign_id','creative_id','channel','kind','publication_time','cost','token'},
            'touch': {'type','id','user_key','placement_id','token','event_type','occurred_at'},
            'lead': {'type','id','user_key','interest','created_at'},
            'order': {'type','id','user_key','lead_id','created_at','items'},
            'payment': {'type','id','order_id','amount','status','occurred_at','evidence'},
        }
        if kind not in schemas or set(e)-schemas[kind]:
            raise DataError('Unknown record type or field')
        ident(e.get('id'))
        if kind == 'placement':
            t,us = self.stamp(ds,e['publication_time'])
            ident(e['campaign_id'],'campaign_id')
            ident(e['creative_id'],'creative_id')
            if not isinstance(e['channel'],str) or not e['channel'].strip():
                raise DataError('channel is required')
            token = e.get('token') or 'p_'+hashlib.sha256(f'{ds}|{e["id"]}'.encode()).hexdigest()[:24]
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',token):
                raise DataError('Invalid tracking token')
            row = dict(dataset_id=ds,id=e['id'],campaign_id=e['campaign_id'],creative_id=e['creative_id'],
                channel=e['channel'],kind=e.get('kind','external'),publication_time=t,publication_us=us,
                cost_cents=None if e.get('cost') is None else cents(e['cost'],False),token=token)
            return self.insert('placements',row,('dataset_id','id'))
        if kind == 'touch':
            if bool(e.get('placement_id')) == bool(e.get('token')):
                raise DataError('Provide exactly one placement_id or tracking token')
            pid = e.get('placement_id')
            if e.get('token'):
                p = self.db.execute('SELECT id FROM placements WHERE dataset_id=? AND token=?',(ds,e['token'])).fetchone()
                if p is None:
                    raise DataError('Unknown tracking token in this dataset')
                pid = p['id']
            p = self.get('placements',ds,pid)
            t,us = self.stamp(ds,e['occurred_at'])
            if us < p['publication_us']:
                raise DataError('Touch cannot precede publication')
            self.ensure_user(ds,e['user_key'])
            row = dict(dataset_id=ds,id=e['id'],user_key=e['user_key'],placement_id=pid,
                event_type=e.get('event_type','bot_start'),occurred_at=t,occurred_us=us)
            return self.insert('touches',row,('dataset_id','id'))
        if kind == 'lead':
            t,us = self.stamp(ds,e['created_at'])
            self.ensure_user(ds,e['user_key'])
            row = dict(dataset_id=ds,id=e['id'],user_key=e['user_key'],interest=str(e.get('interest','')),
                created_at=t,created_us=us)
            return self.insert('leads',row,('dataset_id','id'))
        if kind == 'order':
            t,us = self.stamp(ds,e['created_at'])
            lines = e['items']
            if not isinstance(lines,list) or not lines:
                raise DataError('An order needs at least one item')
            clean = []
            for i,r in enumerate(lines,1):
                if not isinstance(r,dict) or set(r)!={'course','amount'} or not isinstance(r['course'],str) or not r['course'].strip():
                    raise DataError('Every order item needs course and amount')
                clean.append(dict(dataset_id=ds,order_id=e['id'],line_no=i,course=r['course'],
                    amount_cents=cents(r['amount'],False)))
            amount = sum(r['amount_cents'] for r in clean)
            if amount<=0:
                raise DataError('Order total must be positive')
            lead = e.get('lead_id')
            if lead:
                l = self.get('leads',ds,lead)
                if l['user_key']!=e['user_key'] or l['created_us']>us:
                    raise DataError('Lead must belong to the same user and precede the order')
            row = dict(dataset_id=ds,id=e['id'],user_key=e['user_key'],lead_id=lead,
                created_at=t,created_us=us,amount_cents=amount)
            if self.existing('orders',row,('dataset_id','id')):
                old = [dict(x) for x in self.db.execute('SELECT * FROM order_items WHERE dataset_id=? AND order_id=? ORDER BY line_no',(ds,e['id']))]
                if old!=clean:
                    raise DataError('Conflicting order composition')
                return False
            self.ensure_user(ds,e['user_key'])
            self.insert('orders',row,('dataset_id','id'))
            for r in clean:
                self.insert('order_items',r,('dataset_id','order_id','line_no'))
            return True
        if kind == 'payment':
            o = self.get('orders',ds,e['order_id'])
            t,us = self.stamp(ds,e['occurred_at'])
            if us<o['created_us']:
                raise DataError('Payment cannot precede its order')
            status = e.get('status','succeeded')
            policy = self.dataset(ds)['sales_policy']
            evidence = e.get('evidence','recorded_payment')
            if (policy=='legacy_sales_proxy') != (evidence=='sales_proxy'):
                raise DataError('Payment evidence does not match dataset sales policy')
            row = dict(dataset_id=ds,id=e['id'],order_id=e['order_id'],amount_cents=cents(e['amount']),
                status=status,occurred_at=t,occurred_us=us,evidence=evidence)
            if self.existing('payments',row,('dataset_id','id')):
                return False
            if status=='succeeded':
                paid = self.db.execute("SELECT coalesce(sum(amount_cents),0) FROM payments WHERE dataset_id=? AND order_id=? AND status='succeeded'",(ds,e['order_id'])).fetchone()[0]
                if paid+row['amount_cents']>o['amount_cents']:
                    raise DataError('Successful payments exceed the order total')
            return self.insert('payments',row,('dataset_id','id'))

    def load(self, payload):
        if not isinstance(payload,dict) or set(payload)!={'dataset','records'} or not isinstance(payload['records'],list):
            raise DataError('Payload requires dataset and records')
        ds = payload['dataset']
        required = {'id','label','provenance','clock','sales_policy'}
        if not isinstance(ds,dict) or set(ds)!=required:
            raise DataError('Dataset requires id, label, provenance, clock and sales_policy')
        ident(ds['id'],'dataset id')
        new = same = 0
        try:
            with self.db:
                self.insert('datasets',ds,('id',))
                for e in payload['records']:
                    if self.record(ds['id'],e):new+=1
                    else:same+=1
        except (KeyError,TypeError,sqlite3.IntegrityError) as e:
            raise DataError(f'Invalid batch; nothing from this batch was saved: {e}') from e
        return {'dataset':ds['id'],'created_records':new,'unchanged_records':same}

    def report(self, ds, as_of, model='last_touch', days=7, half_life_days=3):
        if model not in MODELS or isinstance(days,bool) or not isinstance(days,int) or not 1<=days<=365 or isinstance(half_life_days,bool) or not isinstance(half_life_days,int) or not 1<=half_life_days<=365:
            raise DataError('Choose a supported model and integer window/half-life of 1-365 days')
        meta = self.dataset(ds)
        end,end_us = self.stamp(ds,as_of)
        pp = [dict(r) for r in self.db.execute('SELECT * FROM placements WHERE dataset_id=? AND publication_us<=? ORDER BY id',(ds,end_us))]
        payments = [dict(r) for r in self.db.execute("SELECT p.*,o.user_key FROM payments p JOIN orders o ON o.dataset_id=p.dataset_id AND o.id=p.order_id WHERE p.dataset_id=? AND p.status='succeeded' AND p.occurred_us<=? ORDER BY p.occurred_us,p.id",(ds,end_us))]
        agg = {p['id']:{'placement_id':p['id'],'channel':p['channel'],'campaign_id':p['campaign_id'],
            'creative_id':p['creative_id'],'kind':p['kind'],'cost_cents':p['cost_cents'],
            'attributed_cents':0,'buyers':set(),'orders':set(),'payments':set()} for p in pp}
        allocations=[]
        unattributed=[]
        window = days*86400*1000000
        for p in payments:
            touches = [dict(r) for r in self.db.execute('SELECT * FROM touches WHERE dataset_id=? AND user_key=? AND occurred_us>=? AND occurred_us<=? ORDER BY occurred_us,id',
                (ds,p['user_key'],p['occurred_us']-window,p['occurred_us']))]
            if not touches:
                unattributed.append({'payment_id':p['id'],'order_id':p['order_id'],'user_key':p['user_key'],
                    'occurred_at':p['occurred_at'],'amount_cents':p['amount_cents'],'reason':'no_observed_touch_in_window'})
                continue
            shares=allocate(touches,p['amount_cents'],p['occurred_us'],model,half_life_days)
            for share in shares:
                pid=share['placement_id'];amount=share['amount_cents']
                a=agg[pid]
                a['attributed_cents']+=amount
                if amount:
                    a['buyers'].add(p['user_key']);a['orders'].add(p['order_id']);a['payments'].add(p['id'])
                allocations.append({'payment_id':p['id'],'order_id':p['order_id'],'user_key':p['user_key'],
                    'placement_id':pid,'amount_cents':amount,'weight_numerator':share['weight_numerator'],'weight_denominator':share['weight_denominator']})
        rows=[]
        for a in agg.values():
            for field in ('buyers','orders','payments'):a[field]=len(a[field])
            cost=a['cost_cents']
            a['romi_pct']=None if cost is None or cost==0 else float(Decimal(a['attributed_cents']-cost)*100/Decimal(cost))
            a['romi_status']='unknown_cost' if cost is None else 'zero_cost' if cost==0 else 'calculated'
            rows.append(a)
        total=sum(p['amount_cents'] for p in payments)
        known=sum(a['attributed_cents'] for a in rows)
        unknown=sum(p['amount_cents'] for p in unattributed)
        if known+unknown!=total:raise RuntimeError('Revenue reconciliation failed')
        complete=bool(rows) and all(r['cost_cents'] is not None for r in rows)
        costs=sum(r['cost_cents'] or 0 for r in rows)
        totals={'sales_cents':total,'attributed_cents':known,'unattributed_cents':unknown,
            'successful_payments':len(payments),'paid_orders':len({p['order_id'] for p in payments}),
            'buyers':len({p['user_key'] for p in payments}),'known_cost_cents':costs,
            'cost_complete':complete,'placements_with_unknown_cost':sum(r['cost_cents'] is None for r in rows),
            'coverage_pct':None if total==0 else float(Decimal(known)*100/Decimal(total)),
            'romi_pct':float(Decimal(known-costs)*100/Decimal(costs)) if complete and costs else None}
        funnel={}
        for table,time_col in [('touches','occurred_us'),('leads','created_us'),('orders','created_us')]:
            funnel[table]=self.db.execute(f'SELECT count(*) FROM {table} WHERE dataset_id=? AND {time_col}<=?',(ds,end_us)).fetchone()[0]
        return {'dataset':meta,'as_of':end,'model':model,'window_days':days,
            'linear_policy':'equal_unique_placements','tie_policy':'time_then_event_id',
            'decay_policy':'latest_touch_per_unique_placement','half_life_days':half_life_days if model=='time_decay' else None,
            'position_policy':'40_first_40_last_20_unique_interior; no_interior_50_50',
            'rounding_policy':'largest_remainder_then_placement_id',
            'totals':totals,'registered_records':funnel,'placements':rows,
            'allocations':allocations,'unattributed_payments':unattributed}


def import_sales(store, path, ds='sales_2026'):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames!=['Номер студента','Сумма','Курс','Время']:
            raise DataError('Expected the four original Russian CSV columns')
        rows=list(reader)
    groups=defaultdict(list)
    for n,r in enumerate(rows,2):
        if not all(r.values()):raise DataError(f'Missing value at CSV row {n}')
        try:t=datetime.strptime(r['Время'],'%d.%m.%Y %H:%M:%S').isoformat()
        except ValueError as e:raise DataError(f'Invalid time at row {n}') from e
        amount=cents(r['Сумма'])
        user='legacy:'+ident(r['Номер студента'],'source_student_id')
        groups[user,t].append({'course':r['Курс'],'amount':rub(amount)})
    records=[]
    for (user,t),items in sorted(groups.items()):
        oid='legacy_'+hashlib.sha256(f'{user}|{t}'.encode()).hexdigest()[:24]
        items=sorted(items,key=lambda x:(x['course'],cents(x['amount'])))
        records.append(dict(type='order',id=oid,user_key=user,created_at=t,items=items))
        records.append(dict(type='payment',id='sale_'+oid,order_id=oid,
            amount=rub(sum(cents(x['amount']) for x in items)),status='succeeded',occurred_at=t,evidence='sales_proxy'))
    result=store.load({'dataset':{'id':ds,'label':'Исторические продажи Поступашек',
        'provenance':'real','clock':'local_unspecified','sales_policy':'legacy_sales_proxy'},'records':records})
    result.update(source_rows=len(rows),inferred_orders=len(groups),source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
    return result


def export_report(r, prefix):
    prefix=Path(prefix)
    prefix.parent.mkdir(parents=True,exist_ok=True)
    prefix.with_suffix('.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['dataset','provenance','as_of','model','window_days','half_life_days','placement_id','channel','campaign_id',
            'creative_id','kind','cost_rub','attributed_rub','buyers','orders','payments','romi_pct','romi_status']
    with prefix.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for a in r['placements']:
            row={k:a[k] for k in ['placement_id','channel','campaign_id','creative_id','kind','buyers','orders','payments','romi_pct','romi_status']}
            row.update(dataset=r['dataset']['id'],provenance=r['dataset']['provenance'],as_of=r['as_of'],
                model=r['model'],window_days=r['window_days'],half_life_days=r['half_life_days'],cost_rub='' if a['cost_cents'] is None else rub(a['cost_cents']),
                attributed_rub=rub(a['attributed_cents']))
            w.writerow(row)


def describe(r):
    t=r['totals']
    return {'dataset':r['dataset']['label'],'provenance':r['dataset']['provenance'],
        'sales_policy':r['dataset']['sales_policy'],'model':r['model'],'window_days':r['window_days'],'half_life_days':r['half_life_days'],
        'sales_rub':rub(t['sales_cents']),'attributed_rub':rub(t['attributed_cents']),
        'unknown_source_rub':rub(t['unattributed_cents']),'paid_orders':t['paid_orders'],
        'successful_payment_records':t['successful_payments'],'coverage_pct':t['coverage_pct'],
        'known_cost_rub':rub(t['known_cost_cents']),'cost_complete':t['cost_complete'],'romi_pct':t['romi_pct']}


def main():
    ap=argparse.ArgumentParser(description='Поступашки: локальный учёт рекламы, касаний и продаж')
    ap.add_argument('--db',type=Path,default=ROOT/'work'/'mvp.sqlite3')
    sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('load',help='Атомарно загрузить события из JSON');p.add_argument('file',type=Path)
    p=sub.add_parser('import-sales',help='Импортировать реальную выгрузку без выдуманной рекламы')
    p.add_argument('file',type=Path);p.add_argument('--dataset',default='sales_2026')
    p=sub.add_parser('report',help='Пересчитать атрибуцию и ROMI')
    p.add_argument('--dataset',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--model',choices=MODELS,default='last_touch');p.add_argument('--days',type=int,default=7)
    p.add_argument('--half-life-days',type=int,default=3)
    p.add_argument('--out',type=Path,default=ROOT/'work'/'report')
    p=sub.add_parser('link',help='Получить токен размещения или ссылку для будущего Telegram-бота')
    p.add_argument('--dataset',required=True);p.add_argument('--placement',required=True);p.add_argument('--bot')
    p=sub.add_parser('demo',help='Запустить весь синтетический сценарий и сравнить пять моделей')
    p.add_argument('--out',type=Path,default=ROOT/'work'/'demo')
    args=ap.parse_args()
    try:
        with Store(args.db) as s:
            if args.cmd=='load':
                ans=s.load(json.loads(args.file.read_text(encoding='utf-8')))
            elif args.cmd=='import-sales':
                ans=import_sales(s,args.file,args.dataset)
            elif args.cmd=='report':
                r=s.report(args.dataset,args.as_of,args.model,args.days,args.half_life_days)
                export_report(r,args.out);ans=describe(r)
            elif args.cmd=='link':
                p=s.get('placements',args.dataset,args.placement)
                ans={'dataset':args.dataset,'placement_id':p['id'],'token':p['token']}
                if args.bot:
                    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',args.bot):raise DataError('Invalid bot username')
                    ans['link']=f'https://t.me/{args.bot}?start={p["token"]}'
                    ans['note']='Ссылка требует подключённого обработчика /start; само открытие ссылки ничего не записывает.'
            elif args.cmd=='demo':
                loaded=s.load(json.loads((DATA_ROOT/'demo.json').read_text(encoding='utf-8')))
                ans={'load':loaded,'reports':[]}
                for model in MODELS:
                    r=s.report('demo_2026','2026-09-15T00:00:00Z',model,7)
                    export_report(r,args.out/model);ans['reports'].append(describe(r))
            print(json.dumps(ans,ensure_ascii=False,indent=2))
    except (DataError,sqlite3.Error,OSError,json.JSONDecodeError) as e:
        print(f'Ошибка: {e}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
