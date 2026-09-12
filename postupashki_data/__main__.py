import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .catalog import FEATURE_NAMES, RAW_TABLES
from .db import connect, initialize, load_json, validate
from .features import build_features, export_features
from .marketing import build_marketing, export_marketing


def main():
    parser = argparse.ArgumentParser(description='Поступашки: база событий и 20 признаков. Без моделей.')
    parser.add_argument('--db', default='data/postupashki.sqlite3', help='Путь к SQLite-файлу')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Создать пустую базу и словарь признаков')
    load = commands.add_parser('ingest', help='Атомарно загрузить JSON из ботов')
    load.add_argument('file')
    build = commands.add_parser('build', help='Рассчитать признаки на выбранный момент UTC')
    build.add_argument('--as-of', required=True)
    export = commands.add_parser('export', help='Выгрузить существующий снимок в CSV')
    export.add_argument('--as-of', required=True)
    export.add_argument('--out', required=True)
    marketing = commands.add_parser('build-marketing', help='Рассчитать каналы и акции на дату')
    marketing.add_argument('--as-of', required=True)
    marketing.add_argument('--horizon-days', type=int, default=30)
    marketing.add_argument('--main-channel')
    report = commands.add_parser('export-marketing', help='Выгрузить статистику каналов/акций')
    report.add_argument('--table', choices=['channel_stats','promotion_stats'], required=True)
    report.add_argument('--as-of', required=True)
    report.add_argument('--horizon-days', type=int, default=30)
    report.add_argument('--main-channel')
    report.add_argument('--out', required=True)
    commands.add_parser('inspect', help='Число исходных строк и снимков')
    commands.add_parser('check', help='Проверить целостность исходной базы')
    args = parser.parse_args()
    if args.command!='init' and not Path(args.db).is_file():
        parser.error('База не существует. Сначала выполните init.')
    con = connect(args.db)
    try:
        if args.command=='init':
            initialize(con)
            result = {'database':str(Path(args.db).resolve()), 'features':len(FEATURE_NAMES), 'status':'initialized'}
        elif args.command=='ingest':
            result = load_json(con, args.file)
        elif args.command=='build':
            result = {'rows':build_features(con,args.as_of),'features':len(FEATURE_NAMES)}
        elif args.command=='export':
            result = {'file':export_features(con,args.as_of,args.out)}
        elif args.command=='build-marketing':
            result = build_marketing(con,args.as_of,args.horizon_days,args.main_channel)
        elif args.command=='export-marketing':
            result = {'file':export_marketing(con,args.table,args.as_of,args.out,args.horizon_days,args.main_channel)}
        elif args.command=='check':
            validate(con)
            integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok':
                raise ValueError(integrity)
            result = {'integrity':integrity}
        else:
            result = {table:con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in [*RAW_TABLES,'feature_definitions','user_features','channel_stats','promotion_stats']}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f'Ошибка: {error}',file=sys.stderr)
        return 1
    finally:
        con.close()
    return 0


if __name__=='__main__':
    sys.exit(main())
