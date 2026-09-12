"""One entrypoint for the collector, canonical data import, and feature builds."""
import argparse
from dataclasses import replace
from pathlib import Path
import json
import logging
import sqlite3
import sys
import time
from config import Config
from store import Store
from telegram_api import APIError
from postupashki_data import utc,build_features,build_marketing


def main():
    p=argparse.ArgumentParser(description='Поступашки: бот + единая БД v2')
    p.add_argument('--db');p.add_argument('--env',default='.env')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init');sub.add_parser('run');sub.add_parser('errors')
    im=sub.add_parser('import');im.add_argument('path');im.add_argument('--watch',type=int)
    bind=sub.add_parser('bind-channel');bind.add_argument('channel_id');bind.add_argument('chat_id',type=int)
    link=sub.add_parser('link');link.add_argument('token');link.add_argument('--bot',required=True)
    inv=sub.add_parser('invite');inv.add_argument('placement_id');inv.add_argument('channel_id');inv.add_argument('--name',default='Рекламное размещение')
    retry=sub.add_parser('retry-update');retry.add_argument('bot_id',type=int);retry.add_argument('update_id',type=int)
    rr=sub.add_parser('retry-replies');rr.add_argument('bot_id',type=int)
    replay=sub.add_parser('replay');replay.add_argument('path');replay.add_argument('--bot-id',type=int,required=True)
    features=sub.add_parser('features');features.add_argument('--as-of')
    fc=sub.add_parser('forecast');fc.add_argument('--as-of');fc.add_argument('--horizon',type=int,default=7)
    bk=sub.add_parser('backup');bk.add_argument('path')
    sub.add_parser('demo')
    args=p.parse_args();cfg=Config.read(args.env,db=args.db)
    if args.command=='demo':
        from demo import generate
        print(generate(args.db or 'data/demo.sqlite3'));return 0
    if args.command=='run':
        from runner import run
        run(cfg);return 0
    with Store(cfg.db) as s:
        s.bind(cfg.dataset)
        if args.command=='init':print(f'Готово: {cfg.db}')
        elif args.command=='import':
            paths=Path(args.path);seen={}
            if not paths.exists():raise ValueError('Файл/папка не существует')
            if args.watch is not None and args.watch<1:raise ValueError('--watch должен быть >=1')
            while True:
                files=sorted(paths.glob('*.json')) if paths.is_dir() else [paths]
                failed=False
                for path in files:
                    import hashlib
                    sig=hashlib.sha256(path.read_bytes()).hexdigest()
                    if seen.get(str(path))==sig:continue
                    try:print(s.import_file(path))
                    except ValueError as e:print(str(e),file=sys.stderr);failed=True
                    seen[str(path)]=sig
                if not args.watch:return int(failed)
                time.sleep(args.watch)
        elif args.command=='bind-channel':
            with s.db:
                s.add('telegram_channels',channel_id=args.channel_id,chat_id=args.chat_id)
                s.validate()
            print('Канал связан')
        elif args.command=='link':
            import re
            if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',args.bot.lstrip('@')):raise ValueError('Неверное имя бота')
            row=s.resolve_token(cfg.dataset,args.token)
            if not row:raise ValueError('Сначала импортируйте tracking_links с этим токеном')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',args.token):raise ValueError('Токен /start должен быть URL-safe и до 64 символов')
            print(f'https://t.me/{args.bot.lstrip("@")}?start={args.token}')
        elif args.command=='invite':
            cfg.require_token()
            from telegram_api import TelegramAPI
            channel=s.db.execute('SELECT chat_id FROM telegram_channels WHERE channel_id=?',(args.channel_id,)).fetchone()
            placement=s.db.execute('SELECT * FROM placements WHERE placement_id=?',(args.placement_id,)).fetchone()
            if not channel or not placement:raise ValueError('Сначала создайте размещение и свяжите канал')
            api=TelegramAPI(cfg.token)
            value=api.call('createChatInviteLink',chat_id=channel[0],name=args.name[:32])
            with s.db:
                s.add('tracking_links',link_id='invite:'+str(value['date']) if 'date' in value else 'invite:'+str(time.time_ns()),
                    source_token=value['invite_link'],source_channel_id=placement['channel_id'],placement_id=args.placement_id,
                    destination_channel_id=args.channel_id,mechanism='invite_link',created_at=utc())
                s.validate()
            print(value['invite_link'])
        elif args.command in ('retry-update','replay'):
            from collector import Collector
            from runner import process_lock
            if args.command=='retry-update':
                row=s.db.execute('SELECT payload_json FROM bot_updates WHERE bot_id=? AND update_id=?',(args.bot_id,args.update_id)).fetchone()
                if not row:raise ValueError('Обновление не найдено')
                updates=[json.loads(row[0])]
            else:
                updates=json.loads(Path(args.path).read_text(encoding='utf-8'));updates=updates if isinstance(updates,list) else [updates]
            with process_lock(str(cfg.db)+f'.{args.bot_id}'):
                bot=Collector(s,cfg,args.bot_id)
                statuses=[bot.ingest(x,retry=args.command=='retry-update') for x in updates]
            print(statuses);return int('failed' in statuses)
        elif args.command=='retry-replies':
            with s.db:s.db.execute("UPDATE bot_outbox SET status='pending',attempts=0,retry_at=0,error=NULL WHERE bot_id=? AND status='failed'",(args.bot_id,))
            print('Ответы возвращены в очередь')
        elif args.command=='features':
            at=args.as_of or utc()
            print({'user_features':build_features(s.db,at),'marketing':build_marketing(s.db,at)})
        elif args.command=='forecast':
            from forecast import build_baselines
            print(build_baselines(s,args.as_of,args.horizon))
        elif args.command=='backup':
            target=Path(args.path)
            if target.exists():raise ValueError('Файл уже существует; выберите новое имя')
            target.parent.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(target) as db:s.db.backup(db)
            print(target)
        elif args.command=='errors':
            for table in ('bot_updates','import_runs','bot_outbox'):
                print(table,[dict(r) for r in s.db.execute(f"SELECT * FROM {table} WHERE status='failed'")])
    return 0


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    try:sys.exit(main())
    except (ValueError,OSError,sqlite3.Error,APIError) as e:print(f'Ошибка: {e}',file=sys.stderr);sys.exit(1)
    except KeyboardInterrupt:print('Остановлено. Данные сохранены.')
