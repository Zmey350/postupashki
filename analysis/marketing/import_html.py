"""Read Telegram Desktop HTML locally; never execute scripts or follow links."""
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime
import argparse
import hashlib
import json
import re

from normalize_export import normalize, write_output


class ExportParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth=0; self.message=None; self.message_depth=None
        self.text_depth=None; self.header_depth=None
        self.messages=[]; self.pagination=[]; self.header=[]; self.services=0

    def handle_starttag(self,tag,attrs):
        a=dict(attrs); c=set(a.get('class','').split())
        if tag=='div':
            self.depth+=1
            if 'page_header' in c: self.header_depth=self.depth
            if 'message' in c:
                if self.message is not None: raise ValueError('Nested message blocks')
                if 'service' in c: self.services+=1
                elif 'default' in c:
                    mid=a.get('id','')
                    if not re.fullmatch(r'message[1-9]\d*',mid): raise ValueError('Invalid HTML message id')
                    self.message={'id':int(mid[7:]),'type':'message','parts':[], 'links':[],
                                  'joined':'joined' in c}
                    self.message_depth=self.depth
            if self.message is not None:
                if {'date','details','pull_right'}<=c and 'date' not in self.message:
                    raw=a.get('title','')
                    try: dt=datetime.strptime(raw,'%d.%m.%Y %H:%M:%S UTC%z')
                    except ValueError as e: raise ValueError(f'Unrecognized timestamp: {raw}') from e
                    self.message['date']=dt.isoformat()
                    self.message['date_raw']=raw
                if c=={'text'}:
                    if self.text_depth is not None: raise ValueError('Nested text blocks')
                    self.text_depth=self.depth
                if 'media_wrap' in c: self.message['media_type']='export_media'
        if tag=='a' and 'pagination' in c: self.pagination.append(a.get('href',''))
        if self.message is not None and self.text_depth is not None:
            if tag=='br': self.message['parts'].append('\n')
            if tag=='a' and a.get('href'): self.message['links'].append(a['href'])
            if tag=='img' and a.get('alt'): self.message['parts'].append(a['alt'])

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag=='div': self.handle_endtag(tag)

    def handle_data(self,data):
        if self.header_depth is not None: self.header.append(data)
        if self.message is not None and self.text_depth is not None:
            self.message['parts'].append(data)

    def handle_endtag(self,tag):
        if tag!='div': return
        if self.text_depth==self.depth: self.text_depth=None
        if self.header_depth==self.depth: self.header_depth=None
        if self.message_depth==self.depth and self.message is not None:
            m=self.message
            if 'date' not in m: raise ValueError(f'Message {m["id"]} has no publication timestamp')
            # Preserve line breaks and inline formatting boundaries; strip only outside whitespace.
            m['text']=''.join(m.pop('parts')).strip()
            m['links']=list(dict.fromkeys(m['links']))
            self.messages.append(m); self.message=None; self.message_depth=None
        self.depth-=1
        if self.depth<0: raise ValueError('Unbalanced div tags')

    def finish(self):
        self.close()
        if self.message is not None or self.depth!=0: raise ValueError('Truncated HTML export')
        title=' '.join(''.join(self.header).split())
        if not title or not self.messages: raise ValueError('No Telegram export header or messages')
        return {'title':title,'messages':self.messages,'pagination':self.pagination,
                'service_blocks':self.services}


def parse_html(text):
    p=ExportParser(); p.feed(text); return p.finish()


def import_files(paths,channel,start=None,end=None,include_text=False):
    if start: datetime.strptime(start,'%Y-%m-%d')
    if end: datetime.strptime(end,'%Y-%m-%d')
    if start and end and start>end: raise ValueError('Start date is after end date')
    messages=[]; sources=[]; titles=set(); hashes=set(); services=0; copies=0
    for path in paths:
        path=Path(path); raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest()
        if sha in hashes: copies+=1; continue
        hashes.add(sha)
        p=parse_html(raw.decode('utf-8-sig'))
        titles.add(p['title']); messages.extend(p['messages']); services+=p['service_blocks']
        sources.append({'filename':path.name,'sha256':sha,'bytes':len(raw),
            'messages':len(p['messages']),'pagination':p['pagination'],
            'first_publication':min(m['date'] for m in p['messages']),
            'last_publication':max(m['date'] for m in p['messages'])})
    if len(titles)!=1: raise ValueError('HTML pages have different channel titles')
    # HTML does not expose the JSON chat type; --channel explicitly identifies the public channel.
    data={'type':'public_channel','messages':messages}
    result=normalize(data,channel,'real_public')
    indexed={m['id']:m for m in messages}
    for row in result['observations']:
        m=indexed[row['post_id']]
        row.update(published_at_source=m['date'],publication_date_local=m['date'][:10],
            original_timestamp=m['date_raw'],links=m['links'],joined=m['joined'])
        if include_text: row['text']=m['text']
    total=len(result['observations'])
    result['observations']=[r for r in result['observations'] if
        (not start or r['publication_date_local']>=start) and
        (not end or r['publication_date_local']<=end)]
    result.update(source_format='telegram_desktop_html',channel_title=next(iter(titles)),
        public_channel_identity='specified_by_caller',source_files=sources,
        source_message_count=total,duplicate_files_ignored=copies,
        skipped_service_blocks=services,selection_start=start,selection_end=end,
        selection_time_basis='source_local_date_with_explicit_offset',
        coverage='provided_export_pages_only; deleted_posts_and_media_contents_unknown')
    return result


def main():
    a=argparse.ArgumentParser(description='Telegram Desktop HTML -> dated public-channel observations')
    a.add_argument('inputs',type=Path,nargs='+'); a.add_argument('--channel',required=True)
    a.add_argument('--from-date'); a.add_argument('--to-date'); a.add_argument('--include-text',action='store_true')
    a.add_argument('--out',type=Path,default=Path('work/html_posts.json'))
    x=a.parse_args()
    try:
        r=import_files(x.inputs,x.channel,x.from_date,x.to_date,x.include_text)
        write_output(r,x.out)
        print(json.dumps({'source_messages':r['source_message_count'],
            'selected_messages':len(r['observations']),'duplicate_files_ignored':r['duplicate_files_ignored'],
            'first':min((p['published_at_source'] for p in r['observations']),default=None),
            'last':max((p['published_at_source'] for p in r['observations']),default=None)},ensure_ascii=False))
    except (ValueError,TypeError,OverflowError,OSError) as e: a.exit(1,f'Error: {e}\n')


if __name__=='__main__': main()
