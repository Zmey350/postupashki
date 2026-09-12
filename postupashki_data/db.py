"""SQLite + атомарная загрузка JSON. Только стандартная библиотека Python."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .catalog import FEATURES, RAW_TABLES


def utc(value=None):
    """Одинаковый формат UTC позволяет точно сравнивать даты в SQLite.

    Дата без времени означает 00:00 UTC. Время без часового пояса запрещено.
    """
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            dt = dt.replace(tzinfo=timezone.utc)
    else:
        raise ValueError('Дата должна быть строкой ISO 8601 или datetime.')
    if dt.tzinfo is None:
        raise ValueError('Укажите часовой пояс: например 2026-09-12T12:00:00Z.')
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def connect(path):
    if str(path) != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=30000')
    return con


def initialize(con):
    version = 0
    existing = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone()
    if existing:
        versions = [r[0] for r in con.execute('SELECT version FROM schema_meta')]
        if versions not in ([1], [2]):
            raise ValueError('Версия БД не поддерживается; нужна явная миграция.')
        version = versions[0]
    if version < 2:
        base = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8') if version == 0 else ''
        extension = Path(__file__).with_name('marketing_schema.sql').read_text(encoding='utf-8')
        try:
            con.executescript('BEGIN IMMEDIATE;\n' + base + extension + '''
                DROP TABLE schema_meta;
                CREATE TABLE schema_meta(version INTEGER PRIMARY KEY CHECK(version=2), description TEXT NOT NULL);
                INSERT INTO schema_meta VALUES(2,'postupashki-data-v2; user features v1 + marketing; RUB kopecks; UTC');
                COMMIT;''')
        except Exception:
            con.rollback()
            raise
    with con:
        con.executemany('INSERT OR IGNORE INTO feature_definitions VALUES(?,?,?,?,?,?,?)',
                        [(i, *f) for i, f in enumerate(FEATURES, 1)])


def record(con, table, row, allowed_tables=None):
    """Добавить строку внутри транзакции ingest. True=new, False=идентичный повтор.

    Чужие поля и изменённые повторы запрещены. Повтор без recorded_at сохраняет
    исходное время регистрации. Глобальные проверки выполняет ingest.
    """
    if table not in (allowed_tables or RAW_TABLES):
        raise ValueError(f'Недопустимая таблица: {table}')
    if not isinstance(row, dict):
        raise ValueError(f'{table}: ожидается объект JSON.')
    columns = {r['name']: r for r in con.execute(f'PRAGMA table_info({table})')}
    if set(row) - columns.keys():
        raise ValueError(f'{table}: неизвестные поля {sorted(set(row)-columns.keys())}')
    row = dict(row)
    for key, value in row.items():
        if value is None:
            continue
        if key.endswith('_at') or key == 'valid_until':
            row[key] = utc(value)
        elif columns[key]['type'] == 'INTEGER':
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f'{table}.{key}: требуется целое число, не float/строка/bool.')
        elif columns[key]['type'] == 'REAL':
            import math
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError(f'{table}.{key}: требуется конечное число.')
        elif columns[key]['type'] == 'TEXT' and not isinstance(value, str):
            raise ValueError(f'{table}.{key}: требуется строка.')
        if (key.endswith('_id') or key in ('title', 'value')) and isinstance(value, str) and not value.strip():
            raise ValueError(f'{table}.{key}: пустая строка недопустима.')
    pk = next(key for key, info in columns.items() if info['pk'])
    if row.get(pk) is None:
        raise ValueError(f'{table}: обязателен {pk}.')
    old = con.execute(f'SELECT * FROM {table} WHERE {pk}=?', (row[pk],)).fetchone()
    if old:
        changes = [k for k, v in row.items() if old[k] != v]
        if changes:
            raise ValueError(f'{table}.{row[pk]}: повтор ID с другими данными ({", ".join(changes)}).')
        return False
    if 'recorded_at' in columns and 'recorded_at' not in row:
        row['recorded_at'] = utc()
    keys = list(row)
    con.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',
                [row[k] for k in keys])
    return True


def validate(con):
    """Проверки, которым необходим весь атомарный пакет, в том числе позиции заказа."""
    for r in con.execute('''SELECT o.order_id,o.total_minor,COUNT(i.item_id) n,
                           COALESCE(SUM(i.line_total_minor),0) s
                           FROM orders o LEFT JOIN order_items i USING(order_id)
                           GROUP BY o.order_id HAVING n=0 OR s!=o.total_minor'''):
        raise ValueError(f'Заказ {r["order_id"]}: сумма позиций должна равняться total_minor, позиции обязательны.')
    for table, time in [('profile_events','occurred_at'), ('acquisition_events','occurred_at'),
                        ('membership_events','occurred_at'), ('activity_events','occurred_at'),
                        ('participation_events','occurred_at'), ('offers','sent_at'), ('orders','ordered_at')]:
        bad = con.execute(f'''SELECT t.* FROM {table} t JOIN users u USING(user_id)
                              WHERE t.{time}<u.first_seen_at LIMIT 1''').fetchone()
        if bad:
            raise ValueError(f'{table}: событие раньше first_seen_at пользователя.')
    bad = con.execute('''SELECT payment_id FROM payments p JOIN orders o USING(order_id)
                         WHERE p.occurred_at<o.ordered_at LIMIT 1''').fetchone()
    if bad:
        raise ValueError('Платёж или возврат не может предшествовать заказу.')
    # Проверяем баланс по временам события И регистрации: возврат не появляется
    # раньше известной оплаты. События с одним timestamp суммируются вместе.
    totals = {r['order_id']:r['total_minor'] for r in con.execute('SELECT order_id,total_minor FROM orders')}
    for clock in ('occurred_at', 'recorded_at'):
        balances = {}
        rows = con.execute(f'''SELECT order_id,{clock},SUM(CASE kind WHEN 'payment'
                                THEN amount_minor ELSE -amount_minor END) amount
                                FROM payments GROUP BY order_id,{clock} ORDER BY order_id,{clock}''')
        for r in rows:
            balance = balances.get(r['order_id'], 0) + r['amount']
            if balance < 0:
                raise ValueError(f'Заказ {r["order_id"]}: возврат превышает доступную оплату ({clock}).')
            if balance > totals[r['order_id']]:
                raise ValueError(f'Заказ {r["order_id"]}: баланс оплат превышает сумму заказа ({clock}).')
            balances[r['order_id']] = balance
    bad = con.execute('''SELECT a.event_id FROM acquisition_events a JOIN placements p USING(placement_id)
                         WHERE a.occurred_at<p.published_at LIMIT 1''').fetchone()
    if bad:
        raise ValueError('Рекламное касание не может предшествовать публикации размещения.')
    bad = con.execute('''SELECT m.event_id FROM membership_events m JOIN channels c USING(channel_id)
                         WHERE c.kind='external' LIMIT 1''').fetchone()
    if bad:
        raise ValueError('Членство собираем только в своих каналах, не в external.')
    bad = con.execute('''SELECT p.placement_id FROM placements p JOIN channels c USING(channel_id)
                         WHERE c.kind!='external' LIMIT 1''').fetchone()
    if bad:
        raise ValueError('Платные внешние размещения должны ссылаться на external-канал.')
    bad = con.execute('''SELECT a.event_id FROM activity_events a JOIN courses c USING(course_id)
                         WHERE a.topic_id IS NOT NULL AND a.topic_id!=c.topic_id LIMIT 1''').fetchone()
    if bad:
        raise ValueError('Тема действия не совпадает с темой курса.')
    if con.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Нарушена ссылочная целостность.')
    from .marketing_validation import validate_marketing
    validate_marketing(con)


def ingest(con, payload):
    """JSON-объект {table: [rows]}; весь пакет добавляется или откатывается целиком.

    Порядок ключей не важен. Заказ и его позиции должны приходить одним пакетом.
    Существующие записи не обновляются: изменения профиля/каталога -- новые версии.
    """
    if not isinstance(payload, dict) or set(payload) - set(RAW_TABLES):
        raise ValueError('Ожидается объект с именами исходных таблиц.')
    counts = {'inserted': 0, 'duplicates': 0}
    with con:
        con.execute('BEGIN IMMEDIATE')
        counts = ingest_rows(con, payload)
    return counts


def load_json(con, path):
    return ingest(con, json.loads(Path(path).read_text(encoding='utf-8')))


def ingest_rows(con, payload, tables=None):
    """Apply inside an existing transaction; used by the unified collector."""
    tables = tables or RAW_TABLES
    if not isinstance(payload, dict) or set(payload)-set(tables):
        raise ValueError("Ожидается объект с именами исходных таблиц.")
    counts = {"inserted": 0, "duplicates": 0}
    existing_orders = {r[0] for r in con.execute('SELECT order_id FROM orders')}
    existing_versions = {r[0] for r in con.execute('SELECT version_id FROM promotion_versions')}
    for table in tables:
        rows = payload.get(table, [])
        if not isinstance(rows, list):
            raise ValueError(f'{table}: ожидается список строк.')
        for row in rows:
            if table == 'promotion_courses' and isinstance(row, dict) and row.get('version_id') in existing_versions:
                if not con.execute('SELECT 1 FROM promotion_courses WHERE link_id=?', (row.get('link_id'),)).fetchone():
                    raise ValueError('Состав продуктов существующей версии акции неизменяем; создайте новую версию вместе с её продуктами.')
            if table == 'order_items' and isinstance(row, dict) and row.get('order_id') in existing_orders:
                if not con.execute('SELECT 1 FROM order_items WHERE item_id=?', (row.get('item_id'),)).fetchone():
                    raise ValueError('Позиции существующего заказа неизменяемы; передавайте новый заказ с позициями одним пакетом.')
            counts['inserted' if record(con, table, row, allowed_tables=tables) else 'duplicates'] += 1
    validate(con)
    return counts
