"""Small .env reader. Environment variables always take precedence."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
from store import DataError, ident


@dataclass(frozen=True)
class Config:
    db: Path = Path('data/postupashki.sqlite3')
    dataset: str = 'telegram_live'
    token: str = ''
    manager: str = ''
    admins: frozenset = frozenset()
    channels: frozenset = frozenset()

    @classmethod
    def read(cls, path='.env', db=None, dataset=None):
        env = {}
        if Path(path).exists():
            for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    raise DataError('Invalid .env line; expected KEY=VALUE')
                key,value = line.split('=',1)
                env[key.strip()] = value.strip().strip('\"\'')
        env.update(os.environ)
        def ids(name, negative=False):
            try:
                result = frozenset(int(x.strip()) for x in env.get(name,'').split(',') if x.strip())
            except ValueError as e:
                raise DataError(f'{name} requires comma-separated numeric IDs') from e
            if any((x >= 0 if negative else x <= 0) for x in result):
                raise DataError(f'Invalid ID sign in {name}')
            return result
        manager = env.get('MANAGER_USERNAME','').lstrip('@')
        if manager and not re.fullmatch(r'[A-Za-z0-9_]{5,32}',manager):
            raise DataError('MANAGER_USERNAME requires a username without a URL')
        return cls(Path(db or env.get('DB_PATH','data/postupashki.sqlite3')),
                   ident(dataset or env.get('DATASET_ID','telegram_live')),
                   env.get('BOT_TOKEN',''),manager,ids('ADMIN_IDS'),ids('CHANNEL_IDS',True))

    def require_token(self):
        if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',self.token):
            raise DataError('Set BOT_TOKEN from BotFather in .env')
