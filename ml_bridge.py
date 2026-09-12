"""Read-only boundary between the standard-library dashboard and optional ML.

Training remains an explicit CLI command. No model imports or deserialization
occur when users view operational dashboards or request ML availability.
"""
import importlib.util
import json
import math
from pathlib import Path
import sqlite3

DEFAULT_MODELS=Path(__file__).parent/'ml/models'
STATUS='Реализовано частично'


def _clean(value):
    if isinstance(value,dict):return {str(k):_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_clean(v) for v in value]
    if hasattr(value,'item'):value=value.item()
    if isinstance(value,float) and not math.isfinite(value):return None
    return value


def status(db_path,model_dir=None):
    models=Path(model_dir) if model_dir else DEFAULT_MODELS
    result=dict(status=STATUS,available=False,reason='',provenance='unknown',
                course_id=None,as_of=None,channels=[],models={},
                note='Модели проверены на синтетике; качество на клиентах Поступашек не установлено.')
    path=Path(db_path).resolve()
    if not path.is_file():
        result['reason']='База ещё не создана';return result
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as con:
            con.row_factory=sqlite3.Row
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sources=[]
            for table in ('app_meta','ml_meta'):
                if table in tables:
                    r=con.execute("SELECT value FROM "+table+" WHERE key='provenance'").fetchone()
                    if r:sources.append(r[0])
            if len(set(sources))>1:
                result['reason']='Конфликт происхождения данных app_meta/ml_meta';return result
            result['provenance']=sources[0] if sources else 'unknown'
            if 'ml_decisions' not in tables:
                result['reason']='ML-таблицы ещё не подключены к этой базе';return result
            course=con.execute('SELECT course_id FROM ml_decisions ORDER BY as_of DESC LIMIT 1').fetchone()
            result['course_id']=course[0] if course else None
            result['channels']=[dict(r) for r in con.execute("SELECT channel_id,title FROM channels WHERE kind='external' ORDER BY channel_id")]
    except sqlite3.Error as exc:
        result['reason']='База недоступна: '+str(exc);return result
    missing=[n for n in ('numpy','pandas','scipy','sklearn','joblib','threadpoolctl') if importlib.util.find_spec(n) is None]
    if missing:
        result['reason']='Установите ML-зависимости: python -m pip install -r ml/requirements.txt';return result
    for task in ('purchase','new_users','new_buyers'):
        try:
            report=json.loads((models/(task+'_report.json')).read_text(encoding='utf-8'))
            meta=report['metadata']
        except (OSError,ValueError,KeyError):
            result['reason']='Модели ещё не обучены: выполните команду train из ml/README.md';return result
        if not (models/(task+'.joblib')).is_file():
            result['reason']='Не найден обученный артефакт '+task;return result
        if meta.get('provenance')!=result['provenance']:
            result['reason']='Модель и база имеют разное происхождение';return result
        result['models'][task]={k:meta.get(k) for k in ('winner','test_metrics','test_baseline_metrics','quality_status','rows','provenance')}
        date=meta.get('evaluated_through') or meta.get('available_at')
        result['as_of']=max(result['as_of'] or date,date)
    if result['provenance']=='synthetic':result['as_of']='2026-09-01'
    result['available']=True
    result['reason']='Доступна демонстрация на синтетических данных' if result['provenance']=='synthetic' else 'Доступен условный прогноз; причинный эффект не установлен'
    return result


def _service(db_path,model_dir,scenario):
    s=status(db_path,model_dir)
    if not s['available']:raise ValueError(s['reason'])
    from postupashki_ml.service import ForecastService,Economics
    economics=Economics(required_roi=float(scenario.get('required_roi',.3)),
        payment_fee_rate=float(scenario.get('payment_fee_rate',0)),
        refund_rate=float(scenario.get('refund_rate',0)),
        fixed_cost_minor=scenario.get('fixed_cost_minor',0),
        cost_per_contact_minor=scenario.get('cost_per_contact_minor',0))
    return ForecastService(db_path,model_dir or DEFAULT_MODELS),economics,s


def predict_customers(db_path,model_dir=None,scenario=None):
    scenario=scenario or {}
    svc,econ,s=_service(db_path,model_dir,scenario)
    result=svc.customers(scenario.get('course_id') or s['course_id'],scenario.get('as_of') or s['as_of'],
                         scenario.get('plan'),economics=econ,include_rows=False)
    result.pop('rows',None)
    result['status']=STATUS
    return _clean(result)


def predict_channels(db_path,model_dir=None,scenario=None):
    scenario=scenario or {}
    svc,econ,s=_service(db_path,model_dir,scenario)
    channel=scenario.get('channel_id')
    if not channel:raise ValueError('Выберите канал с историей размещений')
    result=svc.quote(channel,scenario.get('course_id') or s['course_id'],scenario.get('as_of') or s['as_of'],
        scenario.get('plan'),expected_views=scenario.get('expected_views'),
        incrementality=scenario.get('incrementality'),economics=econ,
        quoted_price_minor=scenario.get('quoted_price_minor'))
    result['status']=STATUS
    return _clean(result)
