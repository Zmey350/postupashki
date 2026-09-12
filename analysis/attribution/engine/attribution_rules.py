"""Explicit allocation rules. Decimal decay weights; exact rational rounding to kopecks."""
from collections import defaultdict
from decimal import Decimal,localcontext
from fractions import Fraction

MODELS=('last_touch','first_touch','linear','time_decay','position_based')
DAY_US=86400*1000000


def allocate(touches,amount,payment_us,model,half_life_days=3):
    if model not in MODELS: raise ValueError('Unknown attribution model')
    if not touches: return []
    ts=sorted(touches,key=lambda t:(t['occurred_us'],t['id']))
    if any(t['occurred_us']>payment_us for t in ts): raise ValueError('Future touch')
    if isinstance(half_life_days,bool) or not isinstance(half_life_days,int) or half_life_days<1:
        raise ValueError('Half-life must be a positive integer number of days')
    ids=sorted({t['placement_id'] for t in ts})
    if model=='first_touch': weights={ts[0]['placement_id']:Fraction(1)}
    elif model=='last_touch': weights={ts[-1]['placement_id']:Fraction(1)}
    elif model=='linear': weights={pid:Fraction(1) for pid in ids}
    elif model=='time_decay':
        last={t['placement_id']:t['occurred_us'] for t in ts}
        with localcontext() as ctx:
            ctx.prec=40
            weights={pid:Fraction(Decimal(2)**(-Decimal(payment_us-last[pid])/Decimal(half_life_days*DAY_US))) for pid in ids}
    else:
        first,last=ts[0]['placement_id'],ts[-1]['placement_id']
        middle=set(ids)-{first,last}; weights=defaultdict(Fraction)
        if not middle:
            weights[first]+=Fraction(1,2);weights[last]+=Fraction(1,2)
        else:
            weights[first]+=Fraction(2,5);weights[last]+=Fraction(2,5)
            for pid in middle: weights[pid]+=Fraction(1,5*len(middle))
    total=sum(weights.values()); weights={pid:w/total for pid,w in weights.items()}
    exact={pid:amount*w for pid,w in weights.items()}
    rounded={pid:x.numerator//x.denominator for pid,x in exact.items()}
    remainder=amount-sum(rounded.values())
    order=sorted(weights,key=lambda pid:(-(exact[pid]-rounded[pid]),pid))
    for pid in order[:remainder]: rounded[pid]+=1
    assert sum(rounded.values())==amount
    return [dict(placement_id=pid,amount_cents=rounded[pid],weight_numerator=weights[pid].numerator,
        weight_denominator=weights[pid].denominator) for pid in sorted(weights)]
