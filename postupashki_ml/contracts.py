from dataclasses import dataclass, field, asdict
import json
import math
import sqlite3
from pathlib import Path
from datetime import timedelta
import pandas as pd
from postupashki_data.db import utc


def stamp(x):
    return utc(x)


def after(x, days):
    return stamp(pd.Timestamp(stamp(x)) + pd.Timedelta(days=days))


def days(a, b):
    return (pd.Timestamp(stamp(a))-pd.Timestamp(stamp(b))).total_seconds()/86400


def read_connection(path):
    path = Path(path).resolve()
    con = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute('PRAGMA table_info(users)')}
    if 'user_id' not in cols or not con.execute("SELECT 1 FROM sqlite_master WHERE name='promotion_versions'").fetchone():
        con.close()
        raise ValueError('Нужна БД postupashki_data v2 (users.user_id + promotion_versions). Старый mvp/user_key не совместим; автоматическая подмена схемы запрещена.')
    return con


def initialize_ml(path):
    with read_connection(path) as con:
        source=provenance(con)
    con.close()
    with sqlite3.connect(path, timeout=30) as con:
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript(Path(__file__).with_name('schema.sql').read_text())
        con.execute("UPDATE ml_meta SET value=? WHERE key='provenance'",(source,))


def provenance(con):
    values=[]
    for table in ('app_meta','ml_meta'):
        if table_exists(con,table):
            row=con.execute("SELECT value FROM "+table+" WHERE key='provenance'").fetchone()
            if row: values.append(row[0])
    if len(set(values))>1:
        raise ValueError('Конфликт происхождения app_meta/ml_meta; проверьте источник базы')
    return values[0] if values else 'unknown'


def table_exists(con, name):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


@dataclass(frozen=True)
class Plan:
    discount_pct: float = 0
    discount_days: int = 14
    contacts: list = field(default_factory=list)

    def validate(self, horizon):
        if not isinstance(horizon, int) or not 1 <= horizon <= 90:
            raise ValueError('horizon_days должен быть целым 1..90')
        if not math.isfinite(self.discount_pct) or not 0 <= self.discount_pct < 100:
            raise ValueError('discount_pct должен быть от 0 до 100, не включая 100')
        if not isinstance(self.discount_days, int) or not 0 <= self.discount_days <= 90:
            raise ValueError('discount_days должен быть целым 0..90')
        if self.discount_pct and not 1 <= self.discount_days <= horizon:
            raise ValueError('Длительность скидки должна укладываться в горизонт')
        for c in self.contacts:
            if set(c)-{'day','kind','intensity'}:
                raise ValueError('Неизвестное поле контакта')
            if c.get('kind') not in ('message','event','ad'):
                raise ValueError('kind контакта: message/event/ad')
            if not isinstance(c.get('day'),int) or not 0 <= c['day'] < horizon:
                raise ValueError('day контакта должен быть целым внутри горизонта')
            if not math.isfinite(c.get('intensity',1)) or not 0 <= c.get('intensity',1) <= 100:
                raise ValueError('intensity должен быть 0..100')
        if len(self.contacts)>100:
            raise ValueError('Не больше 100 контактов на один план')
        return self

    def key(self):
        d = asdict(self)
        if not self.discount_pct:
            d['discount_days'] = 0
        d['contacts'] = sorted([dict(day=c['day'],kind=c['kind'],intensity=c.get('intensity',1)) for c in self.contacts], key=lambda x:(x['day'],x['kind'],x['intensity']))
        return json.dumps(d, sort_keys=True, separators=(',',':'))

    @classmethod
    def parse(cls, value, horizon=14):
        if isinstance(value, cls):
            return value.validate(horizon)
        d = json.loads(value) if isinstance(value,str) else dict(value or {})
        return cls(**d).validate(horizon)


def log_decision(path, decision_id, user_id, course_id, as_of, plan=None,
                 horizon_days=14, assignment_kind='observational', experiment_id=None,
                 arm=None, assignment_probability=None):
    """Call at assignment time, including for controls. Does not send any messages."""
    plan = Plan.parse(plan, horizon_days)
    as_of = stamp(as_of)
    now = stamp()
    row=(decision_id,int(user_id),course_id,as_of,now,horizon_days,plan.key(),assignment_kind,experiment_id,arm,assignment_probability)
    with sqlite3.connect(path,timeout=30) as con:
        con.execute('PRAGMA foreign_keys=ON')
        old=con.execute('SELECT * FROM ml_decisions WHERE decision_id=?',(decision_id,)).fetchone()
        if old:
            if old[:4]+old[5:] != row[:4]+row[5:]:
                raise ValueError('ID решения уже существует с другим содержимым')
            return
        if as_of < now:
            raise ValueError('Решение нельзя задним числом объявить известным: as_of должен быть сейчас или в будущем')
        overlap=con.execute('SELECT as_of,horizon_days FROM ml_decisions WHERE user_id=? AND course_id=?',(user_id,course_id)).fetchall()
        if any(t<after(as_of,horizon_days) and after(t,h)>as_of for t,h in overlap):
            raise ValueError('Перекрывающиеся окна решений для одного пользователя и курса')
        con.execute('INSERT INTO ml_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)',row)


def covered(con, domain, start, end, known_at):
    cursor = stamp(start)
    for a,b in con.execute('SELECT starts_at,ends_at FROM collection_periods WHERE domain=? AND recorded_at<=? AND ends_at>? AND starts_at<? ORDER BY starts_at', (domain,stamp(known_at),cursor,stamp(end))):
        if a>cursor:
            return False
        cursor=max(cursor,b)
        if cursor>=stamp(end):
            return True
    return False


def paid_orders(con, known_at):
    """First full settlement, grouped simultaneous payments, excluding free orders."""
    known_at=stamp(known_at)
    q='''SELECT p.order_id,p.occurred_at,p.kind,p.amount_minor,o.total_minor,o.user_id
         FROM payments p JOIN orders o USING(order_id)
         WHERE p.recorded_at<=? AND p.occurred_at<? AND o.recorded_at<=? AND o.ordered_at<?
         ORDER BY p.order_id,p.occurred_at,p.payment_id'''
    p=pd.read_sql_query(q,con,params=(known_at,known_at,known_at,known_at))
    if p.empty:
        return pd.DataFrame(columns=['order_id','user_id','paid_at','total_minor','balance_minor'])
    p['delta']=p.amount_minor.where(p.kind=='payment',-p.amount_minor)
    g=p.groupby(['order_id','occurred_at'],as_index=False).agg(delta=('delta','sum'),user_id=('user_id','first'),total_minor=('total_minor','first'))
    g['balance_minor']=g.groupby('order_id').delta.cumsum()
    first=g[(g.total_minor>0)&(g.balance_minor>=g.total_minor)].drop_duplicates('order_id')
    last=g.groupby('order_id').balance_minor.last()
    out=first.rename(columns={'occurred_at':'paid_at'})[['order_id','user_id','paid_at','total_minor']].copy()
    out['balance_minor']=out.order_id.map(last)
    return out


def catalog(con, course_id, as_of):
    r=con.execute('''SELECT v.*,c.topic_id FROM course_versions v JOIN courses c USING(course_id)
        WHERE v.course_id=? AND v.effective_at<=? AND v.recorded_at<=? AND c.created_at<? AND c.recorded_at<=?
        ORDER BY v.effective_at DESC,v.recorded_at DESC,v.version_id DESC LIMIT 1''', (course_id,as_of,as_of,as_of,as_of)).fetchone()
    if r is None:
        raise ValueError('Нет известной на дату версии курса '+str(course_id))
    return dict(r)
