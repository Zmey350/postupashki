"""New observed acquisitions and buyers; no regression of historical seller prices."""
import numpy as np
import pandas as pd
from .contracts import stamp,after,days,catalog,paid_orders,covered,table_exists,Plan


class ChannelHistory:
    def __init__(self,con,known_at,arrival_days=7,buyer_days=14):
        self.con=con;self.known_at=stamp(known_at)
        self.arrival_days=arrival_days;self.buyer_days=buyer_days
        self.placements=pd.read_sql_query('SELECT * FROM placements WHERE recorded_at<=? ORDER BY published_at,placement_id',con,params=(self.known_at,))
        self.acq=pd.read_sql_query('''SELECT a.*,u.first_seen_at FROM acquisition_events a JOIN users u USING(user_id)
            WHERE a.occurred_at<? AND a.recorded_at<=? AND u.recorded_at<=?
            ORDER BY a.occurred_at,a.event_id''',con,params=(self.known_at,)*3)
        self.first=self.acq.drop_duplicates('user_id',keep='first')
        self.paid=paid_orders(con,self.known_at).merge(pd.read_sql_query('SELECT DISTINCT order_id,course_id FROM order_items',con),on='order_id')
        self.snapshots=pd.read_sql_query('SELECT * FROM channel_snapshots WHERE recorded_at<=? AND observed_at<? ORDER BY observed_at,recorded_at,snapshot_id',con,params=(self.known_at,self.known_at))
        self.channels=pd.read_sql_query('SELECT * FROM channels WHERE recorded_at<=? AND created_at<?',con,params=(self.known_at,self.known_at)).set_index('channel_id')
        self.plans={r[0]:r[1] for r in con.execute('SELECT placement_id,plan_json FROM ml_placement_plans WHERE recorded_at<=?',(self.known_at,))} if table_exists(con,'ml_placement_plans') else {}

    def outcomes(self,p):
        start=p['published_at'];end=after(start,self.arrival_days)
        a=self.first[(self.first.placement_id==p['placement_id'])&(self.first.occurred_at>=start)&(self.first.occurred_at<end)&(self.first.first_seen_at>=start)]
        # Single first observed route, so a new person cannot be counted in two channels.
        joined=self.paid[self.paid.course_id==p['advertised_course_id']].merge(a[['user_id','occurred_at']],on='user_id')
        if not joined.empty:
            lag=(pd.to_datetime(joined.paid_at,utc=True,format='ISO8601')-pd.to_datetime(joined.occurred_at,utc=True,format='ISO8601')).dt.total_seconds()/86400
            buyers=joined[(lag>=0)&(lag<self.buyer_days)].user_id.nunique()
        else:
            buyers=0
        gross=self.acq[(self.acq.placement_id==p['placement_id'])&(self.acq.occurred_at>=start)&(self.acq.occurred_at<end)].user_id.nunique()
        return {'new_users':len(a),'new_buyers':buyers,'gross_users':gross,
                'day1_users':int((a.occurred_at<after(start,1)).sum()),
                'day3_users':int((a.occurred_at<after(start,3)).sum())}

    def features(self,channel_id,course_id,as_of,decay,plan=None,expected_views=None):
        as_of=stamp(as_of);plan=Plan.parse(plan,self.buyer_days)
        if as_of>self.known_at:
            raise ValueError('ChannelHistory должен быть построен на дату расчёта')
        if channel_id not in self.channels.index:
            raise ValueError('Канал неизвестен на эту дату')
        c=catalog(self.con,course_id,as_of)
        past=self.placements[(self.placements.channel_id==channel_id)&(self.placements.published_at<as_of)&(self.placements.recorded_at<=as_of)]
        mature=past[past.published_at.map(lambda t:after(t,self.arrival_days))<=as_of]
        history=[]
        for p in mature.to_dict('records'):
            if covered(self.con,'acquisition',p['published_at'],after(p['published_at'],self.arrival_days),as_of):
                history.append(self.outcomes(p))
        recent=history[-3:];older=history[-6:-3]
        mean=lambda name,rows:float(np.mean([z[name] for z in rows])) if rows else np.nan
        sn=self.snapshots[(self.snapshots.channel_id==channel_id)&(self.snapshots.observed_at<as_of)&(self.snapshots.recorded_at<=as_of)]
        sn=sn.iloc[-1].to_dict() if len(sn) else {}
        touched=self.acq[(self.acq.occurred_at<as_of)&(self.acq.recorded_at<=as_of)]
        a=set(touched.loc[touched.source_channel_id==channel_id,'user_id'])
        other=set(touched.loc[(touched.source_channel_id!=channel_id)&touched.source_channel_id.notna(),'user_id'])
        intervals=np.array([days(as_of,t) for t in past.published_at])
        result={'channel_id':channel_id,'channel_topic':self.channels.loc[channel_id,'topic_id'] or 'unknown',
                'course_topic':c['topic_id'],'audience_stage':sn.get('audience_stage') or 'unknown',
                'expected_views':expected_views if expected_views is not None else sn.get('typical_post_views_24h',np.nan),
                'subscribers':sn.get('subscribers',np.nan),
                'snapshot_age_days':days(as_of,sn['observed_at']) if sn else np.nan,
                'past_placements':len(past),'mature_placements':len(history),
                'days_since_last':float(intervals.min()) if len(intervals) else np.nan,
                'recent_placements_30d':int((intervals<30).sum()),
                'recent_pressure':float(decay.saturate(decay.weights(intervals).sum())),
                'mean3_new':mean('new_users',recent),'previous_mean3_new':mean('new_users',older),
                'last_new':history[-1]['new_users'] if history else np.nan,
                'mean3_day1':mean('day1_users',recent),'mean3_day3':mean('day3_users',recent),
                'trend_slope':float(np.polyfit(np.arange(len(recent)),[z['new_users'] for z in recent],1)[0]) if len(recent)>=2 else np.nan,
                'observed_responders':len(a),'observed_cross_source_share':len(a&other)/len(a) if a else np.nan,
                'new_share_recent':sum(z['new_users'] for z in recent)/sum(z['gross_users'] for z in recent) if sum(z['gross_users'] for z in recent)>0 else np.nan,
                'course_price':c['regular_price_minor']/100,'days_to_start':days(c['starts_at'],as_of),
                'discount_pct':plan.discount_pct,'discount_days':min(plan.discount_days,self.buyer_days) if plan.discount_pct else 0,
                'weekday':pd.Timestamp(as_of).dayofweek,'month':pd.Timestamp(as_of).month}
        # Conversion history only from completely matured buyer cohorts.
        old=past[past.published_at.map(lambda t:after(t,self.arrival_days+self.buyer_days))<=as_of]
        old=old[old.advertised_course_id==course_id]
        conv=[]
        for p in old.to_dict('records'):
            if covered(self.con,'commerce',p['published_at'],after(p['published_at'],self.arrival_days+self.buyer_days),as_of) and covered(self.con,'acquisition',p['published_at'],after(p['published_at'],self.arrival_days),as_of):
                conv.append(self.outcomes(p))
        result['past_new_buyers']=sum(z['new_buyers'] for z in conv)
        result['past_new_users_mature']=sum(z['new_users'] for z in conv)
        result['past_conversion']=(sum(z['new_buyers'] for z in conv)+1)/(sum(z['new_users'] for z in conv)+20) if conv else np.nan
        for kind in ('message','event','ad'):
            signal=np.zeros(self.buyer_days)
            for contact in plan.contacts:
                if contact['kind']==kind:
                    signal+=contact.get('intensity',1)*decay.weights(np.arange(self.buyer_days)-contact['day'])
            result['planned_'+kind]=float(decay.saturate(signal).mean())
            result['planned_'+kind+'_count']=sum(c.get('intensity',1) for c in plan.contacts if c['kind']==kind)
        return result

    def overlap(self,channels):
        """Pairwise responder overlap: a sample proxy, not subscriber intersection."""
        sets={c:set(self.acq.loc[self.acq.source_channel_id==c,'user_id']) for c in channels}
        return [{'channel_a':a,'channel_b':b,'responders_a':len(sets[a]),'responders_b':len(sets[b]),
                 'intersection':len(sets[a]&sets[b]),'union':len(sets[a]|sets[b]),
                 'jaccard':len(sets[a]&sets[b])/len(sets[a]|sets[b]) if sets[a]|sets[b] else None}
                for i,a in enumerate(channels) for b in channels[i+1:]]


def training_frames(con,cutoff,arrival_days,buyer_days,decays):
    cutoff=stamp(cutoff)
    full=ChannelHistory(con,cutoff,arrival_days,buyer_days)
    frames={v.key():[] for v in decays};targets=[];meta=[];dropped=0
    contexts={}
    for p in full.placements.to_dict('records'):
        t=p['published_at'];end=after(t,arrival_days+buyer_days)
        if end>cutoff or p['recorded_at']>t or not p['advertised_course_id']:
            dropped+=1;continue
        if not covered(con,'acquisition',t,after(t,arrival_days),cutoff) or not covered(con,'commerce',t,end,cutoff):
            dropped+=1;continue
        if t not in contexts:
            contexts[t]=ChannelHistory(con,t,arrival_days,buyer_days)
        ctx=contexts[t]
        plan=Plan.parse(ctx.plans.get(p['placement_id']),buyer_days)
        y=full.outcomes(p)
        for decay in decays:
            frames[decay.key()].append(ctx.features(p['channel_id'],p['advertised_course_id'],t,decay,plan))
        targets.append(y)
        meta.append({'time':t,'end':end,'placement_id':p['placement_id'],'channel_id':p['channel_id']})
    if not targets:
        raise ValueError('Нет зрелых размещений: нужны даты, advertised_course_id и полное наблюдение acquisition/commerce')
    return {k:pd.DataFrame(v) for k,v in frames.items()},pd.DataFrame(targets),pd.DataFrame(meta),{'rows':len(targets),'dropped':dropped}


def portfolio_estimate(quotes, overlaps):
    """Union bounds plus an explicitly approximate responder-overlap scenario.

    New-user predictions already include historical depletion. This correction is
    only for duplication BETWEEN proposed concurrent placements, not history.
    Pairwise overlap cannot identify higher-order intersections.
    """
    ids=[q['channel_id'] for q in quotes]
    if len(ids)!=len(set(ids)):
        raise ValueError('Повтор канала в одном одновременном портфеле не поддерживается')
    n=np.array([q['new_users'] for q in quotes],float)
    m={q['channel_id']:i for i,q in enumerate(quotes)}
    overlap_penalty=0.;known_pairs=0
    for r in overlaps:
        if r['channel_a'] not in m or r['channel_b'] not in m or r['jaccard'] is None:
            continue
        i,j=m[r['channel_a']],m[r['channel_b']]
        # J = I/(A+B-I) => I = J*(A+B)/(1+J), capped at min(A,B).
        overlap_penalty+=min(n[i],n[j],r['jaccard']*(n[i]+n[j])/(1+r['jaccard']))
        known_pairs+=1
    low=float(n.max()) if len(n) else 0.;high=float(n.sum())
    return {'new_users_no_overlap':high,'union_bounds':[low,high],
            'new_users_pairwise_proxy':max(low,high-overlap_penalty) if known_pairs else None,
            'observed_pairs':known_pairs,'missing_pairs':len(n)*(len(n)-1)//2-known_pairs,
            'interpretation':'Сценарная оценка пересечения по наблюдаемым откликам. Границы условны на индивидуальных прогнозах; это не доверительный интервал. Тройные пересечения неизвестны.'}
