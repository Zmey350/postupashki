"""Telegram Bot API transport; no third-party packages or token-bearing logs."""
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class APIError(RuntimeError):
    def __init__(self, code=0, retry_after=0):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f'Telegram API error {code}; check connection, token and bot permissions')


class TelegramAPI:
    def __init__(self, token):
        self._base = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, **payload):
        request = Request(self._base+method,data=json.dumps(payload).encode(),
                          headers={'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=max(15,payload.get('timeout',0)+10)) as response:
                result = json.load(response)
        except HTTPError as e:
            try:
                result = json.loads(e.read())
            except (ValueError,OSError):
                raise APIError(e.code) from None
        except (URLError,OSError,ValueError):
            raise APIError() from None
        if not isinstance(result,dict) or not result.get('ok'):
            if not isinstance(result,dict):
                raise APIError()
            raise APIError(result.get('error_code',0),result.get('parameters',{}).get('retry_after',0))
        return result['result']
