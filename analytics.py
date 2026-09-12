"""Direct, read-only analytics over the canonical SQLite facts. Money stays in kopecks."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
import json
from postupashki_data import utc
from postupashki_data.features import _covered


def dt(x): return datetime.fromisoformat(x.replace('Z','+00:00'))
def add(x,n): return utc(dt(x)+timedelta(days=n))
def ratio(a,b): return a/b if b else None
def romi(revenue,cost): return (revenue-cost)/cost if cost and cost>0 else None
def event_key(r):
    parts=r['event_id'].split(':')
    native=len(parts)>=4 and parts[0]=='tg' and parts[1].isdigit() and parts[2].isdigit()
    return (r['occurred_at'],int(parts[1]) if native else -1,int(parts[2]) if native else -1,r['recorded_at'],r['event_id'])

def group(rows,key):
    out=defaultdict(list)
    for r in rows:out[r[key]].append(r)
    return out


class Analytics:
    def __init__(self,con,start,end,method='last_touch',lookback=90,knowledge=None):
        self.con=con;self.start=utc(start);self.end=utc(end);self.known=utc(knowledge or utc())
        if self.start>=self.end:raise ValueError('Начало периода должно предшествовать концу.')
        if (dt(self.end)-dt(self.start)).days>1096:raise ValueError('Выберите период не длиннее трёх лет.')
        if method not in ('last_touch','first_touch','linear'):raise ValueError('Неизвестная модель атрибуции')
        if not 1<=lookback<=365:raise ValueError('Окно атрибуции: 1–365 дней')
        self.method=method;self.lookback=lookback
        self.channels={r['channel_id']:r for r in self.rows('channels','created_at')}
        self.placements={r['placement_id']:r for r in self.rows('placements','published_at')}
        self.details={r['placement_id']:r for r in self.rows('placement_details')}
        self.promotions={r['promotion_id']:r for r in self.rows('promotions','created_at')}
        self.versions=self.rows('promotion_versions')
        self.pc=group(self.rows('promotion_courses'),'version_id')
        self.pp={r['placement_id']:r['promotion_id'] for r in self.rows('placement_promotions')}
        self.course_versions=group(self.rows('course_versions'),'course_id')
        self.users={r['user_id']:r for r in self.rows('users','first_seen_at')}
        self.orders={r['order_id']:r for r in self.rows('orders','ordered_at')}
        self.items=group(self.rows('order_items'),'order_id')
        self.payments=[r for r in self.rows('payments','occurred_at') if r['order_id'] in self.orders]
        for p in self.payments:
            p['net_minor']=p['amount_minor']*(1 if p['kind']=='payment' else -1)
            p['user_id']=self.orders[p['order_id']]['user_id']
        self.by_order=group(self.payments,'order_id');self.by_user_pay=group(self.payments,'user_id')
        self.order_promos={r['order_id']:r for r in self.rows('order_promotions')}
        self.leads=self.rows('leads','created_at');self.by_user_lead=group(self.leads,'user_id')
        self.by_user_order=group(list(self.orders.values()),'user_id')
        self.members=self.rows('membership_events','occurred_at')
        self.by_member=defaultdict(list)
        for r in self.members:self.by_member[r['user_id'],r['channel_id']].append(r)
        self.hacks={r['hackathon_id']:r for r in self.rows('hackathons')}
        self.parts=self.rows('participation_events','occurred_at');self.completions=self.rows('completion_events','occurred_at')
        self.cost_events=self.rows('placement_cost_events','occurred_at');self.extras=self.rows('cost_components','occurred_at')
        self.snapshots=self.rows('channel_snapshots','observed_at')
        self.periods=defaultdict(list)
        for r in self.rows('collection_periods'):self.periods[r['domain']].append((r['starts_at'],r['ends_at']))
        for ps in self.periods.values():ps.sort()
        self.touches=[]
        for r in self.rows('acquisition_events','occurred_at'):
            self.touches.append(dict(r,confidence='clean' if r['mechanism']!='unknown' and r['source_channel_id'] else 'unknown',kind=r['mechanism']))
        for table,conf,kind in [('manual_sources','mixed','manual_source'),('tracked_clicks','clean','tracked_click')]:
            for r in self.rows(table,'occurred_at'):
                self.touches.append(dict(r,confidence=conf if r['source_channel_id'] else 'unknown',kind=kind))
        self.touches.sort(key=event_key)
        self.by_touch=group(self.touches,'user_id');self.attributions={}
        self.entry={}
        for uid,u in self.users.items():
            ts=self.by_touch[uid]
            t=ts[0] if ts and ts[0]['occurred_at']<=u['first_seen_at'] else None
            self.entry[uid]=dict(user_id=uid,entry_date=u['first_seen_at'],acquisition_source=t['source_channel_id'] if t else None,
                acquisition_type=self.activity_type(t['placement_id']) if t else 'unknown',
                entry_confidence=t['confidence'] if t else 'unknown',placement_id=t['placement_id'] if t else None)

    def rows(self,table,time=None):
        cols={r[1] for r in self.con.execute(f'PRAGMA table_info({table})')}
        if not cols:return []
        where=[];args=[]
        if 'recorded_at' in cols:where.append('recorded_at<=?');args.append(self.known)
        if time:where.append(f'{time}<?');args.append(self.end)
        return [dict(r) for r in self.con.execute(f'SELECT * FROM {table}'+(' WHERE '+' AND '.join(where) if where else ''),args)]

    def covered(self,domain,start,end):return _covered(self.periods,domain,start,end)
    def in_period(self,at):return self.start<=at<self.end
    def title(self,src):return self.channels.get(src,{}).get('title','Без источника')
    def activity_type(self,pid):
        if pid in self.details:return self.details[pid]['activity_type']
        return 'promotion' if pid in self.pp else 'ad' if pid else 'unknown'

    def choose(self,candidates):
        if not candidates:return []
        chosen=candidates[-1:] if self.method=='last_touch' else candidates[:1] if self.method=='first_touch' else candidates
        return [dict(source_channel_id=r['source_channel_id'],placement_id=r.get('placement_id'),
                     promotion_id=self.pp.get(r.get('placement_id')),weight=1/len(chosen),
                     basis=r['kind'],event_id=r['event_id'],occurred_at=r['occurred_at']) for r in chosen]

    def calendar_match(self,o):
        # Conservative fallback: one product, one quantity, unique active discount version,
        # one linked placement. Ambiguous bundles and overlapping promotions stay unassigned.
        items=self.items[o['order_id']]
        if len(items)!=1 or items[0]['quantity']!=1:return []
        at=o['ordered_at'];latest={}
        for v in sorted(self.versions,key=lambda x:(x['effective_at'],x['recorded_at'],x['version_id'])):
            if v['effective_at']<=at and v['recorded_at']<=at:latest[v['promotion_id']]=v
        matches=[]
        for v in latest.values():
            if v['is_cancelled'] or not v['starts_at']<=at<v['ends_at'] or v['discount_type']=='none':continue
            # Without verified profile matches, segmented offers cannot be inferred.
            if v['target_education_stage'] or v['target_job_search_status']:continue
            products=self.pc[v['version_id']]
            if len(products)!=1 or products[0]['course_id']!=items[0]['course_id']:continue
            prices=[r for r in self.course_versions[items[0]['course_id']] if r['effective_at']<=at and r['recorded_at']<=at and not r['is_cancelled']]
            if not prices:continue
            price=max(prices,key=lambda r:(r['effective_at'],r['recorded_at'],r['version_id']))['regular_price_minor']
            expected=(price*(100-v['discount_value'])+50)//100 if v['discount_type']=='percent' else max(0,price-v['discount_value'])
            if expected!=o['total_minor']:continue
            placements=[p for p in self.placements.values() if self.pp.get(p['placement_id'])==v['promotion_id'] and p['published_at']<=at and p['recorded_at']<=at]
            if len(placements)==1:
                p=placements[0]
                matches.append(dict(source_channel_id=p['channel_id'],placement_id=p['placement_id'],promotion_id=v['promotion_id'],weight=1,
                    basis='calendar_price_match',event_id=None,occurred_at=at))
        return matches if len(matches)==1 else []

    def attribute(self,oid,mode):
        key=oid,mode
        if key not in self.attributions:
            o=self.orders[oid];a=add(o['ordered_at'],-self.lookback)
            candidates=[r for r in self.by_touch[o['user_id']] if a<=r['occurred_at']<=o['ordered_at'] and r['source_channel_id'] and
                        (r['confidence']=='clean' or mode=='mixed')]
            self.attributions[key]=self.choose(candidates) or (self.calendar_match(o) if mode=='mixed' else [])
        return self.attributions[key]

    def ad_cost(self,pid):
        rows=[r for r in self.cost_events if r['placement_id']==pid]
        if rows:
            value=sum(r['amount_minor']*(1 if r['kind']=='expense' else -1) for r in rows)
            return value,'actual' if self.covered('ad_costs',self.placements[pid]['published_at'],self.end) else 'recorded_partial'
        val=self.placements[pid]['quoted_cost_minor']
        return val,'quote' if val is not None else 'unknown'

    def discount(self,p):
        # Only explicitly linked promotions with an observed undiscounted price.
        o=self.orders[p['order_id']]
        if o['order_id'] not in self.order_promos:return 0
        regular=0
        for item in self.items[o['order_id']]:
            vs=[r for r in self.course_versions[item['course_id']] if r['effective_at']<=o['ordered_at'] and r['recorded_at']<=o['ordered_at']]
            if not vs:return None
            v=max(vs,key=lambda r:(r['effective_at'],r['recorded_at'],r['version_id']))
            regular+=v['regular_price_minor']*item['quantity']
        return max(0,regular-o['total_minor'])*p['net_minor']/o['total_minor'] if o['total_minor'] else 0

    def cohort_curve(self,entries):
        out=[]
        for n in (7,30,60,90):
            mature=[r for r in entries if add(r['entry_date'],n)<=self.end]
            item=dict(day=n,mature=len(mature),total=len(entries))
            for mode in ('clean','mixed'):
                eligible=[r for r in mature if r['entry_confidence']=='clean' or mode=='mixed' and r['entry_confidence']=='mixed']
                covered=[r for r in eligible if self.covered('commerce',r['entry_date'],add(r['entry_date'],n))]
                buyers=0;revenue=0
                for r in eligible:
                    rows=[p for p in self.by_user_pay[r['user_id']] if r['entry_date']<=p['occurred_at']<add(r['entry_date'],n)]
                    buyers+=any(p['kind']=='payment' for p in rows);revenue+=sum(p['net_minor'] for p in rows)
                item.update({mode+'_eligible':len(eligible),mode+'_observed':len(covered),mode+'_buyers':buyers,
                    mode+'_conversion':ratio(buyers,len(eligible)) if len(covered)==len(eligible) else None,
                    mode+'_revenue_minor':revenue,mode+'_ltv_minor':ratio(revenue,buyers) if len(covered)==len(eligible) else None})
            out.append(item)
        return out

    def membership_at(self,uid,cid,at):
        rows=[r for r in self.by_member[uid,cid] if r['occurred_at']<=at]
        if not rows:return None
        r=max(rows,key=event_key)
        if r['occurred_at']!=at and not self.covered('membership',r['occurred_at'],at):return None
        return r['status']=='joined'

    def report(self):
        selected=[p for p in self.payments if self.in_period(p['occurred_at'])]
        positive=[p for p in selected if p['kind']=='payment']
        total=sum(p['net_minor'] for p in selected)
        mode_totals={m:sum(p['net_minor'] for p in selected if self.attribute(p['order_id'],m)) for m in ('clean','mixed')}
        sources=defaultdict(lambda:dict(clean_minor=0,mixed_minor=0))
        revenue_by_placement=defaultdict(lambda:dict(clean_minor=0,mixed_minor=0))
        days={};d=dt(self.start).date()
        while str(d)<self.end[:10] or str(d)==self.end[:10] and self.end[11:]>'00:00:00.000000Z':
            days[str(d)]=dict(day=str(d),net_minor=0,clean_minor=0,mixed_minor=0,markers=[]);d+=timedelta(days=1)
        for p in selected:
            daily=days[p['occurred_at'][:10]];daily['net_minor']+=p['net_minor']
            for mode in ('clean','mixed'):
                attrs=self.attribute(p['order_id'],mode)
                if attrs:daily[mode+'_minor']+=p['net_minor']
                for a in attrs:
                    amount=p['net_minor']*a['weight'];sources[a['source_channel_id']][mode+'_minor']+=amount
                    if a['placement_id']:revenue_by_placement[a['placement_id']][mode+'_minor']+=amount
        for p in self.placements.values():
            if self.in_period(p['published_at']):days[p['published_at'][:10]]['markers'].append(dict(id=p['placement_id'],kind=self.activity_type(p['placement_id'])))
        for v in self.versions:
            if self.in_period(v['starts_at']) and not v['is_cancelled']:days[v['starts_at'][:10]]['markers'].append(dict(id=v['promotion_id'],kind='promotion'))
        complete_days=[r['net_minor'] for r in days.values() if self.covered('commerce',utc(r['day']),add(utc(r['day']),1))]
        avg=mean(complete_days) if len(complete_days)>=7 else None
        sigma=pstdev(complete_days) if len(complete_days)>=7 else None
        active_intervals=[(v['starts_at'],v['ends_at']) for v in self.versions if not v['is_cancelled']]+[(h['starts_at'],h['ends_at']) for h in self.hacks.values()]
        anomalies=[dict(r,z=(r['net_minor']-avg)/sigma) for r in days.values() if sigma and not r['markers'] and not any(a<add(utc(r['day']),1) and b>utc(r['day']) for a,b in active_intervals) and
                   self.covered('commerce',utc(r['day']),add(utc(r['day']),1)) and abs(r['net_minor']-avg)>2*sigma]
        for r in days.values():r['commerce_complete']=self.covered('commerce',utc(r['day']),add(utc(r['day']),1))
        # Quality percentages are mutually exclusive by positive payment record count.
        qc=sum(bool(self.attribute(p['order_id'],'clean')) for p in positive)
        qm=sum(not self.attribute(p['order_id'],'clean') and bool(self.attribute(p['order_id'],'mixed')) for p in positive)
        active_ps=[p for p in self.placements.values() if self.in_period(p['published_at'])]
        costed=[p for p in active_ps if self.ad_cost(p['placement_id'])[0] is not None]
        quality=dict(payments=len(positive),clean_pct=ratio(qc,len(positive)),mixed_only_pct=ratio(qm,len(positive)),
            unknown_pct=ratio(len(positive)-qc-qm,len(positive)),known_cost_pct=ratio(len(costed),len(active_ps)),
            commerce_complete=self.covered('commerce',self.start,self.end),membership_complete=self.covered('membership',self.start,self.end),
            ad_costs_complete=self.covered('ad_costs',self.start,self.end),
            failed_updates=self.con.execute("SELECT count(*) FROM bot_updates WHERE status='failed'").fetchone()[0],
            failed_imports=self.con.execute("SELECT count(*) FROM import_runs WHERE status='failed'").fetchone()[0],
            failed_replies=self.con.execute("SELECT count(*) FROM bot_outbox WHERE status='failed'").fetchone()[0],
            unmapped_events=sum(r['confidence']=='unknown' and self.in_period(r['occurred_at']) for r in self.touches))
        placements=[]
        all_first={uid:r['entry_date'] for uid,r in self.entry.items()}
        cum_by_channel=defaultdict(set)
        for p in sorted(self.placements.values(),key=lambda r:(r['published_at'],r['placement_id'])):
            pid=p['placement_id'];cid=p['channel_id'];ts=[t for t in self.touches if t['placement_id']==pid and t['confidence']=='clean']
            us={t['user_id'] for t in ts};new={u for u in us if self.entry[u]['placement_id']==pid}
            cum_by_channel[cid]|=us
            snaps=sorted([s for s in self.snapshots if s['channel_id']==cid and s['subscribers'] is not None],key=lambda r:r['observed_at'])
            snap=snaps[-1] if snaps else None
            cer=ratio(len(cum_by_channel[cid]),snap['subscribers']) if snap else None
            prev=[x for x in placements if x['source_channel_id']==cid]
            before=[x for x in snaps if prev and x['observed_at']<=prev[-1]['published_at']]
            grown=(snap['subscribers']>before[-1]['subscribers']) if snap and before else None
            nts=ratio(len(new),len(us));exhausted=None
            if nts is not None and cer is not None and grown is not None:exhausted=nts<0.2 and cer>0.6 and not grown
            ad,cost_status=self.ad_cost(pid)
            extra=sum(x['amount_minor'] for x in self.extras if x['placement_id']==pid)
            discounts=sum((self.discount(pay) or 0)*a['weight'] for pay in selected for a in self.attribute(pay['order_id'],'clean') if a['placement_id']==pid)
            cost=None if ad is None else ad+extra
            rev=revenue_by_placement[pid]
            buyers={x['user_id'] for x in positive if any(a['placement_id']==pid for a in self.attribute(x['order_id'],'clean'))}
            ls={l['lead_id'] for l in self.leads if self.in_period(l['created_at']) and any(t['user_id']==l['user_id'] and t['occurred_at']<=l['created_at'] for t in ts)}
            followers={m['user_id'] for m in self.members if m['status']=='joined' and self.in_period(m['occurred_at']) and any(t['user_id']==m['user_id'] and t['occurred_at']<=m['occurred_at'] for t in ts)}
            inc=[r for r in self.rows('incrementality_evidence') if r['starts_at']<=p['published_at']<r['ends_at'] and (r['placement_id']==pid or r['channel_id']==cid)]
            inc.sort(key=lambda r:(r['method']=='experiment',r['recorded_at']))
            evidence=inc[-1] if inc else None
            inc_value=(evidence['romi_inc'] if evidence['method']=='experiment' else romi(rev['clean_minor']*evidence['k'],cost)) if evidence else None
            placements.append(dict(id=pid,source_channel_id=cid,source=self.title(cid),activity_type=self.activity_type(pid),published_at=p['published_at'],
                ad_minor=ad,extras_minor=extra,discount_minor=discounts,cost_minor=cost,cost_status=cost_status,
                **rev,romi_clean=romi(rev['clean_minor'],cost),romi_mixed=romi(rev['mixed_minor'],cost),
                romi_inc=inc_value,incrementality_method=evidence['method'] if evidence else None,incrementality_description=evidence['description'] if evidence else None,
                touches=len(ts),users=len(us),new_users=len(new),nts=nts,cer=cer,frequency=ratio(len(ts),len(us)),exhausted=exhausted,
                cpa_minor=ratio(cost,len(buyers)) if cost is not None else None,cpl_minor=ratio(cost,len(ls)) if cost is not None else None,
                cpf_minor=ratio(cost,len(followers)) if cost is not None else None,
                in_period=self.in_period(p['published_at'])))
        shown_ps=[p for p in placements if p['in_period'] or p['clean_minor'] or p['mixed_minor']]
        discounts=[self.discount(p) for p in selected]
        extras={c:sum(r['amount_minor'] for r in self.extras if r['component']==c and self.in_period(r['occurred_at'])) for c in ('prize','staff','other')}
        # Cash costs in this period. Quotes are presented separately, never substituted for cash outflow.
        ad_cash=sum(r['amount_minor']*(1 if r['kind']=='expense' else -1) for r in self.cost_events if self.in_period(r['occurred_at']))
        ad_quote=sum(p['quoted_cost_minor'] or 0 for p in active_ps)
        recorded_cost=ad_cash+sum(extras.values())
        total_cost=recorded_cost if quality['ad_costs_complete'] else None
        overview=dict(net_minor=total,gross_minor=sum(p['amount_minor'] for p in positive),refunds_minor=sum(p['amount_minor'] for p in selected if p['kind']=='refund'),
            **mode_totals,delta_minor=mode_totals['mixed']-mode_totals['clean'],ad_minor=ad_cash,quoted_ad_minor=ad_quote,
            discount_minor=sum(x or 0 for x in discounts),discount_unknown=sum(x is None for x in discounts),**{k+'_minor':v for k,v in extras.items()},
            cost_minor=total_cost,recorded_cost_minor=recorded_cost,romi_clean=romi(mode_totals['clean'],total_cost) if quality['ad_costs_complete'] else None,
            romi_mixed=romi(mode_totals['mixed'],total_cost) if quality['ad_costs_complete'] else None,
            buyers=len({p['user_id'] for p in positive}),orders=len({p['order_id'] for p in positive}))
        deals=[]
        for oid in sorted({p['order_id'] for p in selected},key=lambda x:self.orders[x]['ordered_at'],reverse=True):
            o=self.orders[oid];rows=[p for p in selected if p['order_id']==oid]
            deals.append(dict(order_id=oid,user_id=o['user_id'],ordered_at=o['ordered_at'],total_minor=o['total_minor'],
                net_minor=sum(p['net_minor'] for p in rows),payments=rows,items=self.items[oid],
                clean=self.attribute(oid,'clean'),mixed=self.attribute(oid,'mixed'),
                touches=[dict(r,eligible=add(o['ordered_at'],-self.lookback)<=r['occurred_at']<=o['ordered_at']) for r in self.by_touch[o['user_id']]],
                lead_ids=[l['lead_id'] for l in self.by_user_lead[o['user_id']] if l['created_at']<=o['ordered_at']]))
        cohorts=[];cgroup=defaultdict(list)
        for e in self.entry.values():
            if self.in_period(e['entry_date']):cgroup[e['entry_date'][:7],e['acquisition_source'],e['acquisition_type'],e['entry_confidence']].append(e)
        for (month,src,kind,confidence),entries in cgroup.items():
            curve=self.cohort_curve(entries)
            ids={e['placement_id'] for e in entries if e['placement_id']}
            costs=[self.ad_cost(pid)[0] for pid in ids]
            # Allocate each placement cost over all of its first-source users once.
            cohort_cost=sum((self.ad_cost(pid)[0] or 0)*sum(e['placement_id']==pid for e in entries)/max(1,sum(e['placement_id']==pid for e in self.entry.values())) for pid in ids)
            cac=ratio(cohort_cost,curve[-1]['clean_buyers']) if costs and all(x is not None for x in costs) and curve[-1]['mature']==len(entries) and confidence=='clean' and curve[-1]['clean_observed']==curve[-1]['clean_eligible'] else None
            cohorts.append(dict(month=month,source=self.title(src),source_id=src,activity_type=kind,confidence=confidence,size=len(entries),
                entry_min=min(e['entry_date'] for e in entries),entry_max=max(e['entry_date'] for e in entries),curve=curve,cac_minor=cac,
                ltv_cac=ratio(curve[-1]['clean_ltv_minor'],cac) if curve[-1]['clean_ltv_minor'] is not None and cac else None,
                status='горизонт закрыт' if curve[-1]['mature']==len(entries) else 'наблюдается'))
        # True sequential user funnel: every next stage must follow the previous one.
        funnels={}
        for mode in ('clean','mixed'):
            slices=defaultdict(lambda:[set(),set(),set(),set()])
            for uid,ts in self.by_touch.items():
                eligible=[t for t in ts if self.in_period(t['occurred_at']) and t['source_channel_id'] and (mode=='mixed' or t['confidence']=='clean')]
                if not eligible:continue
                t=eligible[0];kind=self.activity_type(t['placement_id']);bucket=slices[kind];bucket[0].add(uid)
                leads=[l for l in self.by_user_lead[uid] if t['occurred_at']<=l['created_at']<self.end]
                if not leads:continue
                lead=min(leads,key=lambda l:l['created_at']);bucket[1].add(uid)
                orders=[o for o in self.by_user_order[uid] if lead['created_at']<=o['ordered_at']<self.end]
                if not orders:continue
                bucket[2].add(uid)
                if any(p['kind']=='payment' and o['ordered_at']<=p['occurred_at']<self.end for o in orders for p in self.by_order[o['order_id']]):bucket[3].add(uid)
            all_sets=[set().union(*(b[i] for b in slices.values())) for i in range(4)]
            funnels[mode]=dict(counts=[len(x) for x in all_sets],by_type=[dict(type=k,counts=[len(x) for x in b]) for k,b in slices.items()])
        growth=[]
        state={};transitions=defaultdict(lambda:Counter(joined=0,left=0,banned=0))
        for r in sorted(self.members,key=event_key):
            key=r['user_id'],r['channel_id'];old=state.get(key);new=r['status']=='joined'
            if r['evidence_kind']=='status_update' and (old is None or old!=new) and self.in_period(r['occurred_at']):
                # Join source is the last observed acquisition at/before this event; leave follows entry cohort.
                src=self.entry.get(r['user_id'],{}).get('acquisition_source')
                transitions[r['occurred_at'][:10],r['channel_id'],src][r['status']]+=1
            state[key]=new
        for (day,cid,src),values in sorted(transitions.items(),key=lambda x:(x[0][0],x[0][1],str(x[0][2]))):
            growth.append(dict(day=day,channel_id=cid,channel=self.title(cid),source=self.title(src),source_id=src,**values,
                               net=values['joined']-values['left']-values['banned']))
        source_metrics=[]
        for cid,c in self.channels.items():
            if c['kind']!='external':continue
            ts=[t for t in self.touches if t['source_channel_id']==cid and t['confidence']=='clean']
            users={t['user_id'] for t in ts}
            snaps=sorted([x for x in self.snapshots if x['channel_id']==cid and x['subscribers'] is not None],key=lambda x:x['observed_at'])
            audience=snaps[-1]['subscribers'] if snaps else None
            source_metrics.append(dict(source_id=cid,source=c['title'],touches=len(ts),users=len(users),frequency=ratio(len(ts),len(users)),
                                       audience=audience,cer=ratio(len(users),audience)))
        events=self.event_report()
        return dict(start=self.start,end=self.end,knowledge_at=self.known,method=self.method,lookback=self.lookback,
            provenance=dict(self.con.execute('SELECT key,value FROM app_meta')).get('provenance','real'),overview=overview,quality=quality,
            daily=list(days.values()),sources=[dict(source=self.title(k),source_id=k,**v) for k,v in sources.items()],
            anomalies=anomalies,placements=shown_ps,growth=growth,snapshots=self.snapshots,
            channels=list(self.channels.values()),events=events,funnel=funnels,cohorts=sorted(cohorts,key=lambda r:r['month'],reverse=True),
            deals=deals,source_metrics=source_metrics,forecasts=self.forecasts(),errors=self.errors(),feature_stats={t:self.con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('users','user_features','channel_stats','promotion_stats')})

    def event_report(self):
        definitions=[];latest={}
        for v in sorted(self.versions,key=lambda x:(x['effective_at'],x['recorded_at'],x['version_id'])):
            if v['effective_at']<self.end:latest[v['promotion_id']]=v
        for pid,promo in self.promotions.items():
            v=latest.get(pid)
            if not v or v['is_cancelled']:continue
            ps={pid2 for pid2,pro in self.pp.items() if pro==pid}
            offers={r['offer_id']:r for r in self.rows('offers','sent_at') if r['status']=='sent'}
            versions={r['version_id'] for r in self.versions if r['promotion_id']==pid}
            linked=[offers[r['offer_id']] for r in self.rows('promotion_offers') if r['version_id'] in versions and r['offer_id'] in offers]
            definitions.append(dict(id=pid,title=promo['title'],kind=promo['kind'],starts_at=v['starts_at'],ends_at=v['ends_at'],placements=ps,offers=linked))
        for hid,h in self.hacks.items():
            definitions.append(dict(id=hid,title=h['title'],kind='hackathon',starts_at=h['starts_at'],ends_at=h['ends_at'],
                placements={pid for pid,r in self.details.items() if r['hackathon_id']==hid},offers=[]))
        for p in self.placements.values():
            if self.activity_type(p['placement_id'])=='warmup' and p['placement_id'] not in self.pp:
                definitions.append(dict(id=p['placement_id'],title='Гайд · '+self.title(p['channel_id']),kind='warmup',starts_at=p['published_at'],
                    ends_at=self.details.get(p['placement_id'],{}).get('ends_at') or add(p['published_at'],1),placements={p['placement_id']},offers=[]))
        result=[]
        for e in definitions:
            if e['ends_at']>=self.end:continue
            members={}
            for t in self.touches:
                if t['placement_id'] in e['placements'] and t['source_channel_id']:
                    members.setdefault(t['user_id'],dict(user_id=t['user_id'],entry_date=t['occurred_at'],entry_confidence=t['confidence']))
            for o in e['offers']:
                members.setdefault(o['user_id'],dict(user_id=o['user_id'],entry_date=o['sent_at'],entry_confidence='clean'))
            parts=[r for r in self.parts if r['hackathon_id']==e['id']] if e['kind']=='hackathon' else []
            for r in parts:
                if r['status']=='registered':members.setdefault(r['user_id'],dict(user_id=r['user_id'],entry_date=r['occurred_at'],entry_confidence='clean'))
            curve=self.cohort_curve(list(members.values()))
            costs=[self.ad_cost(pid)[0] for pid in e['placements']]
            ad=sum(x or 0 for x in costs) if costs and all(x is not None for x in costs) else None
            extra=[r for r in self.extras if r['promotion_id']==e['id'] or r['hackathon_id']==e['id'] or r['placement_id'] in e['placements']]
            components={c:sum(r['amount_minor'] for r in extra if r['component']==c) for c in ('prize','staff','other')}
            discount=sum(self.discount(p) or 0 for p in self.payments if p['order_id'] in self.order_promos and
                any(v['version_id']==self.order_promos[p['order_id']]['version_id'] and v['promotion_id']==e['id'] for v in self.versions))
            cost=ad+sum(components.values()) if ad is not None else None
            # Same fully aged population for the 7-to-90-day difference.
            matured=[r for r in members.values() if add(r['entry_date'],90)<=self.end and r['entry_confidence']=='clean']
            awakened=sum(p['net_minor'] for r in matured for p in self.by_user_pay[r['user_id']] if add(r['entry_date'],7)<=p['occurred_at']<add(r['entry_date'],90))
            for point in curve:
                for mode in ('clean','mixed'):
                    point['romi_'+mode]=romi(point[mode+'_revenue_minor'],cost) if point['mature']==point['total'] and point[mode+'_observed']==point[mode+'_eligible'] else None
            registered={r['user_id'] for r in parts if r['status']=='registered'}
            active={r['user_id'] for r in parts if r['status']=='solution_submitted'}&registered
            complete={r['user_id'] for r in self.completions if r['hackathon_id']==e['id']}&active
            own=[r['channel_id'] for r in self.channels.values() if r['kind']=='own_main']
            retained=set();retention_eligible=set();retention_observed=set()
            target=add(e['ends_at'],30)
            if target<self.end and len(own)==1:
                retention_eligible=complete
                for uid in complete:
                    state=self.membership_at(uid,own[0],target)
                    if state is not None:retention_observed.add(uid)
                    if state:retained.add(uid)
            buyers={uid for uid in members if any(p['kind']=='payment' and members[uid]['entry_date']<=p['occurred_at']<add(members[uid]['entry_date'],90) for p in self.by_user_pay[uid])}
            # Final sequential stage requires purchase after the 30-day membership checkpoint.
            retained_buyers={uid for uid in retained if any(p['kind']=='payment' and target<=p['occurred_at'] for p in self.by_user_pay[uid])}
            lead_users={uid for uid,r in members.items() if any(r['entry_date']<=l['created_at']<add(r['entry_date'],90) for l in self.by_user_lead[uid])}
            promoted_awakened=sum(p['net_minor'] for r in matured for p in self.by_user_pay[r['user_id']] if p['order_id'] in self.order_promos and add(r['entry_date'],7)<=p['occurred_at']<add(r['entry_date'],90))
            result.append(dict(id=e['id'],title=e['title'],kind=e['kind'],starts_at=e['starts_at'],ends_at=e['ends_at'],
                audience=len(members),leads=len(lead_users),cpl_minor=ratio(cost,len(lead_users)) if cost is not None else None,promoted_awakened_minor=promoted_awakened if matured else None,romi_inc=None,curve=curve,ad_minor=ad,discount_minor=discount,**{k+'_minor':v for k,v in components.items()},cost_minor=cost,
                awakened_minor=awakened if matured else None,awakened_mature=len(matured),
                registered=len(registered),active=len(active),completed=len(complete),retained30=len(retained) if retention_eligible and retention_eligible==retention_observed else None,
                retention_eligible=len(retention_eligible),retention_observed=len(retention_observed),buyers=len(buyers),retained_buyers=len(retained_buyers),
                cost_registered=ratio(cost,len(registered)) if cost is not None else None,cost_active=ratio(cost,len(active)) if cost is not None else None,
                cost_completed=ratio(cost,len(complete)) if cost is not None else None,cost_buyer=ratio(cost,len(buyers)) if cost is not None else None,
                in_period=self.in_period(e['ends_at'])))
        return sorted(result,key=lambda r:r['ends_at'],reverse=True)

    def forecasts(self):
        runs=[]
        for r in self.con.execute('SELECT * FROM forecast_runs WHERE created_at<=? ORDER BY origin_at DESC,model',(self.known,)):
            r=dict(r);r['known_campaigns']=json.loads(r.pop('known_campaigns_json'))
            points=[]
            for p in self.con.execute('SELECT * FROM forecast_points WHERE run_id=? ORDER BY day',(r['run_id'],)):
                p=dict(p);start=utc(p['day']);end=add(start,1)
                p['actual_minor']=sum(x['net_minor'] for x in self.payments if start<=x['occurred_at']<end) if end<=self.end and self.covered('commerce',start,end) else None
                points.append(p)
            r['points']=points;runs.append(r)
        return runs

    def errors(self):
        updates=[dict(r) for r in self.con.execute("SELECT bot_id,update_id,received_at,error FROM bot_updates WHERE status='failed' ORDER BY received_at DESC LIMIT 50")]
        imports=[dict(r) for r in self.con.execute("SELECT filename,received_at,error FROM import_runs WHERE status='failed' ORDER BY received_at DESC LIMIT 50")]
        return dict(updates=updates,imports=imports)


def report(con,start,end,method='last_touch',lookback=90,knowledge=None):
    # One consistent SQLite read snapshot while a collector/importer writes.
    con.execute('BEGIN')
    try:return Analytics(con,start,end,method,lookback,knowledge).report()
    finally:con.rollback()
