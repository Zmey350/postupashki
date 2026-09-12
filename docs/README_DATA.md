# БД и признаки: справочник команд

**Статус сквозного продукта: «Реализовано частично».** Схема, атомарный импорт, проверки и расчёт признаков исполняются; полноценный поток реальных событий бизнеса ещё не подключён. Этот пакет обслуживает общий проект, а не отдельный продукт без МЛ. Бизнес-допущения, статусы и общий запуск — в [основном README](../README.md); сбор событий — в [ботах](../bots/README.md), прогнозы — в [МЛ](../ml/README.md).

Команды выполняются **из корня проекта**, на проверенном Python 3.12. Ядро требует Python 3.11+ и не использует сторонних библиотек. Параметр `--db` ставится **перед** подкомандой. Все восемь подкоманд ниже лично проверены на отдельной копии поставляемой синтетической базы.

## Пример на копии демобазы

Создайте отдельную копию, пока приложение не использует `work/data_check.sqlite3`. Команда SQLite backup сохраняет согласованный снимок исходной базы:

```bash
python -c "import sqlite3; from pathlib import Path; Path('work').mkdir(exist_ok=True); s=sqlite3.connect('data/demo.sqlite3'); d=sqlite3.connect('work/data_check.sqlite3'); s.backup(d); d.close(); s.close()"
```

```bash
python -m postupashki_data --db work/data_check.sqlite3 init
python -m postupashki_data --db work/data_check.sqlite3 inspect
python -m postupashki_data --db work/data_check.sqlite3 ingest ingest_template.json
python -m postupashki_data --db work/data_check.sqlite3 build --as-of 2026-09-01
python -m postupashki_data --db work/data_check.sqlite3 export --as-of 2026-09-01 --out work/user_features.csv
python -m postupashki_data --db work/data_check.sqlite3 build-marketing --as-of 2026-09-01 --horizon-days 30
python -m postupashki_data --db work/data_check.sqlite3 export-marketing --table channel_stats --as-of 2026-09-01 --horizon-days 30 --out work/channels.csv
python -m postupashki_data --db work/data_check.sqlite3 export-marketing --table promotion_stats --as-of 2026-09-01 --horizon-days 30 --out work/promotions.csv
python -m postupashki_data --db work/data_check.sqlite3 check
```

`init` создаёт схему или применяет миграцию, не удаляя записи. `ingest_template.json` — пустой шаблон; его загрузка проверяет формат, но не добавляет события. `inspect` показывает числа записей; `check` проверяет ограничения и целостность SQLite. `build` записывает 20 базовых пользовательских признаков, `build-marketing` — статистику каналов и акций. ML отдельно расширяет признаки под свои задачи. Экспорт содержит индивидуальные строки: пример синтетический, выгрузки реальных пользователей нельзя включать в публичную сдачу.

Для новой пустой БД используйте другое имя; последующие команды до импорта реальных событий дадут пустые результаты:

```bash
python -m postupashki_data --db work/new.sqlite3 init
```

Дата без времени — начало суток UTC: `--as-of 2026-09-01` не включает события самого 1 сентября. Можно передать полную ISO-дату с часовым поясом. Для отдельных собственных каналов у `build-marketing` и `export-marketing` доступен одинаковый `--main-channel <channel_id>`; дата, горизонт и канал экспорта должны совпадать с рассчитанным снимком.

Полные поля и ограничения: [схема](schema_reference.md), [20 базовых признаков](features.md), [каналы и акции](marketing.md), [контракт событий](bot_contract.md). В рабочей интеграции `bot.py import` дополнительно принимает таблицы заявок и служебной части общей платформы. Наличие идентичного ID с изменёнными данными не считается безопасным повтором и отклоняется.
