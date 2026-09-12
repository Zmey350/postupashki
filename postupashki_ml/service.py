"""Public Python API shared by CLI, dashboard and batch jobs."""
from dataclasses import dataclass
from pathlib import Path
import json
import math
import joblib
import numpy as np
import pandas as pd
import sklearn
from .contracts import read_connection,stamp,after,Plan,provenance,catalog
from .customers import CustomerContext
from .channels import ChannelHistory,portfolio_estimate


@dataclass(frozen=True)
class Economics:
    required_roi: float = .3
    payment_fee_rate: float = 0
    refund_rate: float = 0
    fixed_cost_minor: int = 0
    cost_per_contact_minor: int = 0

    def __post_init__(self):
        if not math.isfinite(self.required_roi) or self.required_roi<0:
            raise ValueError('required_roi must be finite and nonnegative')
        for v in (self.payment_fee_rate,self.refund_rate):
            if not math.isfinite(v) or not 0<=v<=1:
                raise ValueError('Rates must be between 0 and 1')
        if any(not isinstance(v,int) or v<0 for v in (self.fixed_cost_minor,self.cost_per_contact_minor)):
            raise ValueError('Costs must be nonnegative integer kopecks')


class ForecastService:
    def __init__(self, db, models):
        self.db=str(db);self.models=Path(models);self.cache={}

    def artifact(self, name, as_of, prov):
        if name not in self.cache:
            self.cache[name]=joblib.load(self.models/(name+'.joblib'))
        a=self.cache[name]
        built=a.metadata.get('versions',{}).get('sklearn',sklearn.__version__)
        if built.split('.')[:2]!=sklearn.__version__.split('.')[:2]:
            raise ValueError(f'Модель обучена в scikit-learn {built}; установите requirements-tested.txt или переобучите модель в текущем окружении')
        if a.metadata['provenance']!=prov:
            raise ValueError('Модель и база имеют разное происхождение: синтетическую модель нельзя незаметно применить к реальным данным')
        if stamp(as_of)<a.metadata.get('available_at',a.metadata['trained_through']):
            raise ValueError('Прогноз раньше даты доступности обученной модели/калибровки')
        return a

    def customers(self,course_id,as_of,plan=None,user_ids=None,batch_size=10000,
                  economics=None,out_csv=None,include_rows=False):
        economics=economics or Economics()
        if not isinstance(batch_size,int) or batch_size<1:raise ValueError('batch_size должен быть положительным целым')
        con=read_connection(self.db)
        try:
            prov=provenance(con);a=self.artifact('purchase',as_of,prov)
            h=a.metadata['horizon_days'];plan=Plan.parse(plan,h)
            ctx=CustomerContext(con,course_id,as_of,h)
            if ctx.course['is_cancelled'] or not ctx.course['sales_open_at']<=stamp(as_of)<ctx.course['sales_close_at']:
                raise ValueError('Продажи курса закрыты или курс отменён')
            if ctx.course['sales_close_at']<after(as_of,h):
                raise ValueError('Горизонт выходит за закрытие продаж: нужна модель с подходящим горизонтом')
            if user_ids is None:
                ids=ctx.eligible
            else:
                ids=pd.Index([int(u) for u in user_ids])
                if not ids.is_unique:raise ValueError('Повторяющиеся user_id')
                missing=ids.difference(ctx.base.index)
                if len(missing):raise ValueError(f'{len(missing)} пользователей неизвестны на дату')
                ids=ids[ids.isin(ctx.eligible)]
            n=len(ids);total0=total1=revenue0=revenue1=low0=low1=high0=high1=0.
            rows=[];extrapolation={};unknown={}
            if out_csv:
                Path(out_csv).parent.mkdir(parents=True,exist_ok=True)
                pd.DataFrame(columns=['user_id','p_current','p_scenario','difference']).to_csv(out_csv,index=False)
            for start in range(0,n,batch_size):
                part=ids[start:start+batch_size]
                x0,pr0=ctx.features(a.decay,Plan(),part)
                x1,pr1=ctx.features(a.decay,plan,part)
                p0=a.predict(x0,batch_size);p1=a.predict(x1,batch_size)
                total0+=p0.sum();total1+=p1.sum()
                revenue0+=np.dot(p0,pr0.price_mean_minor);revenue1+=np.dot(p1,pr1.price_mean_minor)
                low0+=np.dot(p0,pr0.price_min_minor);low1+=np.dot(p1,pr1.price_min_minor)
                high0+=np.dot(p0,pr0.price_max_minor);high1+=np.dot(p1,pr1.price_max_minor)
                frame=pd.DataFrame({'user_id':part,'p_current':p0,'p_scenario':p1,'difference':p1-p0})
                if out_csv:frame.to_csv(out_csv,mode='a',header=False,index=False)
                if include_rows:rows.extend(frame.to_dict('records'))
                ext=a.extrapolation(x1)
                for k,v in ext['numeric_outside_training_range'].items():extrapolation[k]=extrapolation.get(k,0)+v
                for k,v in ext['unseen_categories'].items():unknown.setdefault(k,set()).update(v)
            cost=0 if plan.key()==Plan().key() else economics.fixed_cost_minor+n*sum(c.get('intensity',1) for c in plan.contacts)*economics.cost_per_contact_minor
            unit_cost=ctx.course['variable_cost_minor']
            def contribution(revenue,count):
                return None if unit_cost is None else float(revenue*(1-economics.refund_rate-economics.payment_fee_rate)-count*unit_cost)
            m0=contribution(revenue0,total0);m1=contribution(revenue1,total1)
            return dict(provenance=prov,interpretation='synthetic_demo' if prov=='synthetic' else 'conditional_scenario_not_causal',
                        course_id=course_id,as_of=stamp(as_of),horizon_days=h,eligible_users=n,
                        expected_buyers_current=float(total0),expected_buyers_scenario=float(total1),difference_buyers=float(total1-total0),
                        expected_revenue_current_uniform_timing_minor=float(revenue0),expected_revenue_scenario_uniform_timing_minor=float(revenue1),
                        revenue_bounds_current_minor=[float(low0),float(high0)],revenue_bounds_scenario_minor=[float(low1),float(high1)],
                        contribution_current_minor=m0,contribution_scenario_before_campaign_cost_minor=m1,campaign_cost_minor=float(cost),
                        difference_contribution_after_campaign_cost_minor=None if m0 is None else float(m1-m0-cost),
                        contribution_roi=None if m0 is None or cost==0 else float((m1-m0-cost)/cost),
                        plan_seen_in_training=plan.key() in a.metadata.get('trained_plans',[]),
                        extrapolation={'numeric_outside_training_range':extrapolation,'unseen_categories':{k:sorted(v) for k,v in unknown.items()}},
                        model=a.metadata['winner'],test_metrics=a.metadata['test_metrics'],
                        quality_status=a.metadata.get('quality_status','not_evaluated'),
                        assumptions=['Одна покупка выбранного курса на человека за горизонт; уже оплаченный невозвращённый курс исключён.',
                                     'При меняющейся цене точечная выручка предполагает равномерное время покупки; границы отражают диапазон цен, не статистическую неопределённость.',
                                     'Сравнение планов — модельный сценарий. Причинный эффект требует проверки на рандомизированных назначениях.',
                                     'Публичная публикация не означает индивидуальное прочтение; план задаёт доступность/доставку контактов.',
                                     'Другие курсы и перенос покупок за горизонт не включены; цена/условия вне истории обозначены как экстраполяция.'],
                        rows=rows if include_rows else None)
        finally:
            con.close()

    def quote(self,channel_id,course_id,as_of,plan=None,expected_views=None,
              incrementality=None,economics=None,quoted_price_minor=None):
        economics=economics or Economics()
        if expected_views is not None and (not np.isfinite(expected_views) or expected_views<0):raise ValueError('expected_views должен быть неотрицательным')
        if incrementality is not None and (not np.isfinite(incrementality) or not 0<=incrementality<=1):raise ValueError('incrementality должен быть 0..1')
        if quoted_price_minor is not None and (not isinstance(quoted_price_minor,int) or quoted_price_minor<0):raise ValueError('Цена в целых неотрицательных копейках')
        con=read_connection(self.db)
        try:
            prov=provenance(con);new=self.artifact('new_users',as_of,prov);buy=self.artifact('new_buyers',as_of,prov)
            h=buy.metadata['horizon_days'];arrival=buy.metadata['arrival_days']
            if new.metadata['horizon_days']!=h or new.metadata['arrival_days']!=arrival:
                raise ValueError('Артефакты притока и покупок используют разные горизонты; переобучите оба в одной папке')
            plan=Plan.parse(plan,h)
            if plan.discount_pct and plan.discount_days!=h:
                raise ValueError('Оценка рекламы сейчас поддерживает скидку на весь buyer-horizon; для коротких скидок используйте клиентский сценарий с границами выручки')
            hist=ChannelHistory(con,as_of,arrival,h)
            fn=hist.features(channel_id,course_id,as_of,new.decay,plan,expected_views)
            fb=hist.features(channel_id,course_id,as_of,buy.decay,plan,expected_views)
            if fn['mature_placements']<1:
                raise ValueError('Нет ни одного завершённого наблюдаемого размещения в канале')
            c=catalog(con,course_id,stamp(as_of))
            if c['is_cancelled'] or not c['sales_open_at']<=stamp(as_of)<c['sales_close_at']:
                raise ValueError('Продажи курса закрыты')
            if after(as_of,arrival+h)>c['sales_close_at']:
                raise ValueError('Курс закроет продажи раньше завершения рекламного горизонта')
            n=float(new.predict(pd.DataFrame([fn]))[0]);b=min(n,float(buy.predict(pd.DataFrame([fb]))[0]))
            band=None if buy.residual_radius is None else [max(0,b-buy.residual_radius),min(n,b+buy.residual_radius)]
            price=c['regular_price_minor']*(1-plan.discount_pct/100)
            cost=c['variable_cost_minor']
            margin=None if cost is None else price*(1-economics.refund_rate-economics.payment_fee_rate)-cost
            contact_cost=n*sum(t.get('intensity',1) for t in plan.contacts)*economics.cost_per_contact_minor
            other_cost=economics.fixed_cost_minor+contact_cost
            def ceiling(buyers,factor):
                return None if margin is None or factor is None else max(0,float(buyers*margin*factor/(1+economics.required_roi)-other_cost))
            limit=ceiling(b,incrementality)
            return dict(channel_id=channel_id,course_id=course_id,as_of=stamp(as_of),provenance=prov,
                        arrival_days=arrival,buyer_days=h,new_users=n,new_buyers=b,buyers_empirical_error_band=band,
                        attributed_revenue_minor=b*price,unit_contribution_minor=margin,
                        attributed_price_ceiling_minor=ceiling(b,1),assumed_incrementality=incrementality,
                        maximum_price_minor=limit,conservative_maximum_price_minor=ceiling(band[0],incrementality) if band else None,
                        required_roi=economics.required_roi,quoted_price_minor=quoted_price_minor,
                        meets_assumed_roi=None if limit is None or quoted_price_minor is None else bool(quoted_price_minor<=limit and b*(margin or 0)*(incrementality or 0)>=(quoted_price_minor+other_cost)*(1+economics.required_roi)),
                        history=fn,extrapolation=buy.extrapolation(pd.DataFrame([fb])),
                        quality_status={k:v.metadata.get('quality_status','not_evaluated') for k,v in [('new_users',new),('new_buyers',buy)]},
                        interpretation='synthetic_demo' if prov=='synthetic' else 'attributed_outcome_with_explicit_incrementality_assumption',
                        notes=['Это максимальная выгодная цена при заданной марже/ROI, не прогноз прайса владельца канала.',
                               'Первый наблюдаемый источник исключает двойной учёт новых людей; повторные касания учитываются в истории.',
                               'Наблюдаемые пересечения откликов не равны пересечениям всех подписчиков внешних каналов.',
                               'Без доли дополнительных покупок maximum_price_minor остаётся null; attributed_price_ceiling предполагает 100% дополнительных покупок.',
                               'Цена условна на будущем охвате и условиях курса. Доход от реактивации старых клиентов не включён.'])
        finally:
            con.close()

    def portfolio(self,channels,course_id,as_of,**kwargs):
        if not channels or len(channels)!=len(set(channels)):raise ValueError('Нужен непустой список уникальных каналов')
        quotes=[self.quote(c,course_id,as_of,**kwargs) for c in channels]
        con=read_connection(self.db)
        try:
            hist=ChannelHistory(con,as_of)
            ov=hist.overlap(channels)
        finally:
            con.close()
        return {'placements':quotes,'overlaps':ov,'portfolio':portfolio_estimate(quotes,ov)}

    def compare_plans(self,course_id,as_of,plans,**kwargs):
        results=[self.customers(course_id,as_of,p,**kwargs) for p in plans]
        scores=[r['difference_contribution_after_campaign_cost_minor'] for r in results]
        best=None if any(v is None for v in scores) else int(np.argmax(scores))
        return {'scenarios':results,'best_scenario_index':best,'selection_rule':'Максимальная модельная разница вклада после расходов среди переданных планов; условна на допущениях, не доказанный оптимум.'}
