"""Normalize a Telegram Desktop single-channel JSON export. Python 3.10+, stdlib only."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import csv
import hashlib
import json
import re


def flatten(x):
    if isinstance(x,str): return x
    if isinstance(x,list): return ''.join(flatten(v) for v in x)
    if isinstance(x,dict) and 'text' in x: return flatten(x['text'])
    raise ValueError('Unsupported text structure; review the export format instead of dropping content')


def timestamp(m):
    raw=m.get('date_unixtime')
    if raw is not None:
        if isinstance(raw,bool) or not re.fullmatch(r'-?\d+',str(raw)):
            raise ValueError('Invalid date_unixtime')
        return datetime.fromtimestamp(int(raw),timezone.utc).isoformat(),'unix_seconds_utc'
    if not isinstance(m.get('date'),str): raise ValueError('Message has no usable date')
    d=datetime.fromisoformat(m['date'].replace('Z','+00:00'))
    if d.tzinfo is None: return d.isoformat(),'local_timezone_unknown'
    return d.astimezone(timezone.utc).isoformat(),'explicit_offset_utc'


def tags(text):
    t=text.casefold()
    rules={
        'discount_candidate':r'скидк|распродаж|промокод',
        'launch_candidate':r'запускаем|открываем.*направлен|набор.*курс',
        'webinar_candidate':r'вебинар|прямой эфир',
        'free_event_candidate':r'бесплатн|открыт\w* недел',
        'sale_candidate':r'записаться|записи.*курс|пишите менеджеру|напишите менеджеру',
        'third_party_ad_candidate':r'\bреклама\b|\berid\b',
    }
    return [name for name,pattern in rules.items() if re.search(pattern,t)] or ['content_unclassified']


def normalize(data,channel,provenance='real_public'):
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',channel): raise ValueError('Invalid public channel username')
    if not isinstance(data,dict) or not isinstance(data.get('messages'),list):
        raise ValueError('Expected one channel export with a messages array')
    if data.get('type')!='public_channel':
        raise ValueError('This importer accepts public_channel exports only')
    if provenance not in ('real_public','synthetic'): raise ValueError('Invalid provenance')
    if data.get('_synthetic') and provenance!='synthetic':
        raise ValueError('The demo must be labelled synthetic')
    rows={}; seen={}; skipped=duplicates=0
    for m in data['messages']:
        if not isinstance(m,dict): raise ValueError('Invalid message')
        if m.get('type')!='message': skipped+=1; continue
        mid=m.get('id')
        if isinstance(mid,bool) or not isinstance(mid,int) or mid<=0: raise ValueError('Invalid message id')
        signature=json.dumps(m,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        if mid in seen:
            if seen[mid]!=signature: raise ValueError(f'Conflicting duplicate message {mid}')
            duplicates+=1;continue
        seen[mid]=signature
        body=flatten(m.get('text',''))
        dt,basis=timestamp(m)
        row=dict(observation_id=f'{channel}:{mid}',channel=channel,post_id=mid,
            source_url=f'https://t.me/{channel}/{mid}',provenance=provenance,
            published_at=dt,time_basis=basis,edited_at=m.get('edited'),
            text_sha256=hashlib.sha256(body.encode()).hexdigest(),text_length=len(body),
            media_only=not body.strip(),has_media=any(k in m for k in ('photo','file','media_type')),
            candidate_tags=tags(body),review_status='pending',marketing_target='unknown',
            activity_start=None,activity_end=None,marketing_cost_rub=None)
        # These are mentions, including salaries and third-party prices; never ad costs.
        row['money_mentions']=re.findall(r'\d[\d\s\u00a0]*(?:[.,]\d{1,2})?\s*(?:₽|руб\w*)',body,flags=re.I)
        rows[mid]=row
    return dict(channel=channel,provenance=provenance,coverage='export_scope_unverified',
        skipped_service_messages=skipped,duplicate_messages_ignored=duplicates,
        observations=[rows[k] for k in sorted(rows)])


def write_output(result,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)
    cols=['observation_id','source_url','provenance','published_at','time_basis','edited_at',
        'text_sha256','text_length','has_media','media_only','candidate_tags','money_mentions','review_status']
    with path.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader()
        for r in result['observations']:
            w.writerow({k:json.dumps(r[k],ensure_ascii=False) if isinstance(r[k],list) else r[k] for k in cols})


def main():
    a=argparse.ArgumentParser(description='Telegram Desktop JSON -> журнал кандидатов для ручной проверки')
    a.add_argument('input',type=Path);a.add_argument('--channel',required=True)
    a.add_argument('--provenance',choices=['real_public','synthetic'],default='real_public')
    a.add_argument('--out',type=Path,default=Path('work/imported_posts.json'))
    x=a.parse_args()
    try:
        raw=x.input.read_bytes()
        result=normalize(json.loads(raw.decode('utf-8-sig')),x.channel,x.provenance)
        result['source_sha256']=hashlib.sha256(raw).hexdigest()
        write_output(result,x.out)
        print(json.dumps({k:result[k] for k in ('channel','provenance','coverage','skipped_service_messages','duplicate_messages_ignored')},ensure_ascii=False))
        print(f'Posts: {len(result["observations"])}; saved: {x.out}')
    except (ValueError,TypeError,OverflowError,OSError) as e:
        a.exit(1,f'Error: {e}\n')


if __name__=='__main__':main()
