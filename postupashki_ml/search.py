"""Budgeted joint feature/model search with purged temporal tuning/calibration/test."""
from dataclasses import dataclass
import importlib.util
import json
import math
from pathlib import Path
import warnings
import sys
import sklearn
import numpy as np
import pandas as pd
import joblib
from scipy.special import logit
from sklearn.base import BaseEstimator,TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression,PoissonRegressor
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor,ExtraTreesClassifier,ExtraTreesRegressor
from sklearn.dummy import DummyClassifier,DummyRegressor
from sklearn.metrics import log_loss,brier_score_loss,average_precision_score,roc_auc_score,mean_absolute_error,mean_squared_error
from threadpoolctl import threadpool_limits


class NativeFrame(BaseEstimator,TransformerMixin):
    def __init__(self,categories=None):
        self.categories=categories
    def fit(self,x,y=None):
        return self
    def transform(self,x):
        x=x.copy()
        for c in x:
            x[c]=x[c].fillna('unknown').astype(str) if c in self.categories else pd.to_numeric(x[c],errors='coerce')
        return x


def candidate_specs(task,full=False):
    binary=task=='purchase'
    specs=[('baseline',{}),('linear',{'regularization':.1}),('hist',{'depth':3,'l2':5}),('extra',{'leaf':15,'features':.8})]
    if full:
        specs += [('linear',{'regularization':x}) for x in (.01,1,10)]
        specs += [('hist',{'depth':d,'l2':l}) for d in (3,5) for l in (1,10)]
        specs += [('extra',{'leaf':v,'features':.8}) for v in (5,30)]
    skipped=[]
    for name in ('catboost','lightgbm'):
        if importlib.util.find_spec(name):
            specs.append((name,{'depth':4,'iterations':180}))
            if full:
                specs.append((name,{'depth':6,'iterations':300}))
        else:
            skipped.append(name+' не установлен; установите extra [boosting]')
    return specs,skipped


def model(task,name,params,x,seed,n_jobs):
    binary=task=='purchase'
    cat=[c for c in x if x[c].dtype==object or isinstance(x[c].dtype,pd.CategoricalDtype)]
    num=[c for c in x if c not in cat]
    num_pipe=Pipeline([('missing',SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True)),('scale',StandardScaler())])
    pre=ColumnTransformer([('num',num_pipe,num),('cat',Pipeline([('missing',SimpleImputer(strategy='constant',fill_value='unknown')),('onehot',OneHotEncoder(handle_unknown='ignore',sparse_output=False))]),cat)])
    if name=='baseline':
        est=DummyClassifier(strategy='prior') if binary else DummyRegressor(strategy='mean')
    elif name=='linear':
        est=LogisticRegression(C=params['regularization'],max_iter=800,random_state=seed) if binary else PoissonRegressor(alpha=params['regularization'],max_iter=500)
    elif name=='hist':
        options=dict(max_iter=130,max_depth=params['depth'],l2_regularization=params['l2'],early_stopping=False,random_state=seed)
        est=HistGradientBoostingClassifier(**options) if binary else HistGradientBoostingRegressor(loss='poisson',**options)
    elif name=='extra':
        options=dict(n_estimators=100,min_samples_leaf=params['leaf'],max_features=params['features'],random_state=seed,n_jobs=n_jobs)
        est=ExtraTreesClassifier(**options) if binary else ExtraTreesRegressor(**options)
    elif name=='catboost':
        from catboost import CatBoostClassifier,CatBoostRegressor
        pre=NativeFrame(cat)
        options=dict(cat_features=cat,depth=params['depth'],iterations=params['iterations'],thread_count=n_jobs,verbose=False,allow_writing_files=False,random_seed=seed)
        est=CatBoostClassifier(loss_function='Logloss',**options) if binary else CatBoostRegressor(loss_function='Poisson',**options)
    elif name=='lightgbm':
        from lightgbm import LGBMClassifier,LGBMRegressor
        options=dict(max_depth=params['depth'],num_leaves=2**params['depth'],n_estimators=params['iterations'],n_jobs=n_jobs,verbosity=-1,random_state=seed)
        est=LGBMClassifier(**options) if binary else LGBMRegressor(objective='poisson',**options)
    else:
        raise ValueError(name)
    return Pipeline([('prepare',pre),('model',est)])


def raw_predict(est,x,binary):
    if binary:
        out=est.predict_proba(x)
        classes=est.named_steps['model'].classes_
        if 1 not in classes:
            return np.full(len(x),1e-7)
        return np.clip(out[:,list(classes).index(1)],1e-7,1-1e-7)
    return np.maximum(est.predict(x),0)


def metrics(y,p,binary):
    if not binary:
        return dict(mae=float(mean_absolute_error(y,p)),rmse=float(mean_squared_error(y,p)**.5),actual_total=float(np.sum(y)),predicted_total=float(np.sum(p)))
    n=max(1,int(math.ceil(len(y)*.1)));top=np.argsort(p)[-n:]
    return dict(logloss=float(log_loss(y,p,labels=[0,1])),brier=float(brier_score_loss(y,p)),
                average_precision=float(average_precision_score(y,p)) if np.sum(y) else None,
                roc_auc=float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,
                precision_top10=float(np.mean(y[top])),base_rate=float(np.mean(y)),
                predicted_buyers=float(np.sum(p)),actual_buyers=int(np.sum(y)))


def time_splits(meta):
    dates=sorted(meta.time.unique())
    if len(dates)<12:
        raise ValueError('Нужно минимум 12 разных дат наблюдений для временного подбора, калибровки и теста')
    n=len(dates)
    a,b,c,d=[dates[min(n-1,int(n*f))] for f in (.4,.57,.73,.87)]
    def ix(mask):
        return np.flatnonzero(mask.to_numpy())
    folds=[(ix(meta.end<=a),ix((meta.time>=a)&(meta.end<=b))),
           (ix(meta.end<=b),ix((meta.time>=b)&(meta.end<=c)))]
    fit=ix(meta.end<=c)
    calibration=ix((meta.time>=c)&(meta.end<=d))
    test=ix(meta.time>=d)
    if any(len(z)==0 for pair in folds for z in pair) or min(len(fit),len(calibration),len(test))==0:
        raise ValueError('Недостаточно календарной истории после исключения перекрывающихся окон. Нужны более длинные данные, а не случайное разбиение.')
    return folds,fit,calibration,test,dict(tuning_boundaries=[a,b,c],calibration_start=c,test_start=d)


@dataclass
class Artifact:
    task: str
    estimator: object
    decay: object
    columns: list
    calibration: object
    residual_radius: object
    metadata: dict

    def predict(self,x,batch_size=10000,n_jobs=2):
        if batch_size<1:
            raise ValueError('batch_size must be positive')
        x=x.reindex(columns=self.columns)
        pieces=[]
        with threadpool_limits(limits=n_jobs):
            for start in range(0,len(x),batch_size):
                p=raw_predict(self.estimator,x.iloc[start:start+batch_size],self.task=='purchase')
                if self.calibration is not None:
                    p=self.calibration.predict_proba(logit(np.clip(p,1e-7,1-1e-7)).reshape(-1,1))[:,1]
                pieces.append(p)
        return np.concatenate(pieces) if pieces else np.array([])

    def extrapolation(self,x):
        r={}
        for c,limits in self.metadata.get('numeric_ranges',{}).items():
            if c in x and limits[0] is not None:
                f=pd.to_numeric(x[c],errors='coerce')
                count=int(((f<limits[0])|(f>limits[1])).sum())
                if count:r[c]=count
        unknown={c:sorted(set(x[c].dropna().astype(str))-set(vals)) for c,vals in self.metadata.get('categories',{}).items() if c in x}
        return {'numeric_outside_training_range':r,'unseen_categories':{k:v for k,v in unknown.items() if v}}


def run_search(task,frames,y,meta,decays,out,provenance,horizon,max_trials=20,n_jobs=2,seed=42,full=False):
    if not 1<=n_jobs<=32 or max_trials<4:
        raise ValueError('n_jobs: 1..32; max_trials: минимум 4')
    binary=task=='purchase';y=np.asarray(y)
    if binary and len(np.unique(y))<2:
        raise ValueError('Для покупки нужны и покупатели, и непокупатели')
    if not binary and np.sum(y)<=0:
        raise ValueError('Целевая переменная целиком нулевая: прогноз не обучается')
    folds,fit,cal,test,boundaries=time_splits(meta)
    specs,skipped=candidate_specs(task,full)
    # All model families are included before extra transforms/hyperparameters.
    trials=[];seen=set()
    for n,p in specs:
        if n not in seen:
            trials.append((decays[0],n,p));seen.add(n)
    if max_trials<len(trials):
        raise ValueError(f'max_trials должен быть не меньше числа установленных семейств моделей: {len(trials)}')
    rest=[(dc,n,p) for dc in decays for n,p in specs if n!='baseline' and (dc,n,p) not in trials]
    rng=np.random.default_rng(seed);rng.shuffle(rest)
    trials=(trials+rest)[:max_trials]
    results=[]
    with threadpool_limits(limits=n_jobs):
        for i,(dc,name,params) in enumerate(trials):
            x=frames[dc.key()]
            losses=[];error=None
            try:
                for tr,va in folds:
                    if binary and len(np.unique(y[tr]))<2:
                        raise ValueError('В обучающем временном окне только один класс')
                    est=model(task,name,params,x,seed,n_jobs)
                    est.fit(x.iloc[tr],y[tr])
                    p=raw_predict(est,x.iloc[va],binary)
                    losses.append(float(log_loss(y[va],p,labels=[0,1]) if binary else mean_absolute_error(y[va],p)))
            except Exception as e:
                error=type(e).__name__+': '+str(e)
            result=dict(trial=i,model=name,parameters=params,decay=dc.key(),fold_scores=losses,score=float(np.mean(losses)) if losses and error is None else None,error=error)
            results.append(result)
            print(f'{task}: {i+1}/{len(trials)} {name} {dc.key()} score={result["score"]}',flush=True)
    valid=[r for r in results if r['score'] is not None and np.isfinite(r['score'])]
    if not valid:
        raise ValueError('Все модели завершились ошибкой: '+json.dumps(results,ensure_ascii=False))
    winner=min(valid,key=lambda r:r['score'])
    dc,name,params=trials[winner['trial']];x=frames[dc.key()]
    est=model(task,name,params,x,seed,n_jobs)
    with threadpool_limits(limits=n_jobs):
        est.fit(x.iloc[fit],y[fit])
        cp=raw_predict(est,x.iloc[cal],binary)
    calibration=None;radius=None;calibration_note='none'
    if binary:
        if len(cal)>=100 and min(np.sum(y[cal]),len(cal)-np.sum(y[cal]))>=15:
            calibration=LogisticRegression(C=1,max_iter=500)
            calibration.fit(logit(cp).reshape(-1,1),y[cal])
            if calibration.coef_[0,0]<=0:
                calibration=None;calibration_note='disabled: temporal calibration inverted ranking'
            else:
                calibration_note='sigmoid fitted on separate chronological calibration period'
        else:
            calibration_note='insufficient calibration outcomes; raw probabilities retained'
    elif len(cal)>=10:
        q=min(1,math.ceil((len(cal)+1)*.9)/len(cal))
        radius=float(np.quantile(np.abs(y[cal]-cp),q,method='higher'))
    numeric={c:[float(x.iloc[fit][c].min()),float(x.iloc[fit][c].max())] if x.iloc[fit][c].notna().any() else [None,None] for c in x if x[c].dtype!=object}
    cats={c:sorted(x.iloc[fit][c].dropna().astype(str).unique().tolist()) for c in x if x[c].dtype==object}
    m=dict(provenance=provenance,horizon_days=horizon,trained_through=max(meta.iloc[fit].end),
           versions={'python':sys.version.split()[0],'sklearn':sklearn.__version__,'numpy':np.__version__,'pandas':pd.__version__},
           available_at=max(meta.iloc[cal].end),
           evaluated_through=max(meta.end),winner=winner,skipped_models=skipped,split=boundaries,
           rows=dict(fit=len(fit),calibration=len(cal),test=len(test)),calibration=calibration_note,
           numeric_ranges=numeric,categories=cats,seed=seed,
           interval_note='Empirical out-of-time absolute-error band; no coverage guarantee under drift',
           interpretation='synthetic_demo' if provenance=='synthetic' else 'observational_prediction')
    if binary:
        m['trained_plans']=sorted(meta.iloc[fit].plan.unique().tolist())
        from .customers import experiment_report
        m['experiments']=experiment_report(meta,y)
    artifact=Artifact(task,est,dc,list(x.columns),calibration,radius,m)
    tp=artifact.predict(x.iloc[test],n_jobs=n_jobs)
    m['test_metrics']=metrics(y[test],tp,binary)
    baseline=model(task,'baseline',{},x,seed,n_jobs);baseline.fit(x.iloc[fit],y[fit])
    m['test_baseline_metrics']=metrics(y[test],raw_predict(baseline,x.iloc[test],binary),binary)
    criterion='logloss' if binary else 'mae'
    m['beats_test_baseline']=bool(m['test_metrics'][criterion]<m['test_baseline_metrics'][criterion])
    m['quality_status']='better_than_baseline_on_test' if m['beats_test_baseline'] else 'baseline_is_better_on_test'
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    joblib.dump(artifact,out/(task+'.joblib'),compress=3)
    (out/(task+'_report.json')).write_text(json.dumps({'metadata':m,'trials':results},ensure_ascii=False,indent=2,allow_nan=False))
    pred=meta.iloc[test].reset_index(drop=True).copy();pred['actual']=y[test];pred['prediction']=tp
    pred.to_csv(out/(task+'_test_predictions.csv'),index=False)
    if binary:
        bins=pd.cut(tp,bins=np.linspace(0,1,11),include_lowest=True)
        pd.DataFrame({'bin':bins,'prediction':tp,'actual':y[test]}).groupby('bin',observed=True).agg(n=('actual','size'),predicted=('prediction','mean'),observed=('actual','mean')).to_csv(out/(task+'_calibration.csv'))
    return artifact
