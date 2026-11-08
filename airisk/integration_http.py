"""Bounded OAuth transport for configured enterprise services."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, Request

MAX_RESPONSE = 2 * 1024 * 1024


class IntegrationError(Exception):
    def __init__(self, message, *, ambiguous=False, retry_after=60, status=None):
        super().__init__(message)
        self.ambiguous = ambiguous
        self.retry_after = max(1, min(retry_after, 86400))
        self.status = status


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward bearer tokens or client credentials to a redirect.


def retry_delay(value):
    try:
        return max(1, min(int(value), 86400))
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
            return max(1, min(int((when - datetime.now(timezone.utc)).total_seconds()), 86400))
        except (TypeError, ValueError, OverflowError):
            return 60


class OAuthClient:
    service = "Integration"
    error_type = IntegrationError

    def __init__(self, config, opener=None):
        self.config = config
        self.opener = opener or build_opener(NoRedirects())
        self.token = None
        self.token_until = 0

    def _request(self, url, *, data=None, headers=None, mutating=False, method=None):
        request = Request(url, data=data, headers={'Accept': 'application/json', **(headers or {})},
                          method=method or ('POST' if data is not None else 'GET'))
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise self.error_type(f'{self.service} returned a response larger than the allowed limit.', ambiguous=mutating)
        except HTTPError as exc:
            # Do not log remote response bodies; they may echo secrets or private ticket data.
            status = exc.code
            delay = retry_delay(exc.headers.get('Retry-After'))
            exc.close()
            message = {400: f'{self.service} rejected the request. Check the configured fields and permissions.',
                       401: f'{self.service} authentication failed. Check the client credentials and scopes.',
                       403: f'{self.service} denied access. Check the integration agent permissions.',
                       404: f'{self.service} could not find this resource. Check its ID and the configured API URL.',
                       429: f'{self.service} rate limit reached. Retry after the displayed retry time.'}.get(
                           status, f'{self.service} returned HTTP {status}.')
            raise self.error_type(message, ambiguous=mutating and status not in (400, 401, 403, 404, 422, 429),
                            retry_after=delay, status=status) from None
        except (URLError, OSError, HTTPException):
            raise self.error_type(f'{self.service} could not be reached securely or the request timed out.', ambiguous=mutating) from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise self.error_type(f'{self.service} returned an invalid JSON response.', ambiguous=mutating) from None

    def _access_token(self):
        if self.token and time.monotonic() < self.token_until:
            return self.token
        body = urlencode({'grant_type': 'client_credentials', 'client_id': self.config.client_id,
                          'client_secret': self.config.client_secret, 'scope': self.config.scope}).encode()
        result = self._request(self.config.token_url, data=body,
                               headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if not isinstance(result, dict):
            raise self.error_type(f'{self.service} returned an invalid authentication response.')
        token = result.get('access_token')
        if (not isinstance(token, str) or not token or len(token) > 16000 or not token.isascii()
                or any(ord(c) < 33 or ord(c) == 127 for c in token)):
            raise self.error_type(f'{self.service} returned an invalid access token.')
        try:
            expiry = max(0, min(float(result.get('expires_in', 300)) - 30, 86400))
        except (TypeError, ValueError, OverflowError):
            raise self.error_type(f'{self.service} returned an invalid token expiry.') from None
        self.token, self.token_until = token, time.monotonic() + expiry
        return token

    def call(self, path, *, payload=None, query=None, method=None):
        url = self.config.api_url + path + ('?' + urlencode(query) if query else '')
        for attempt in range(2):
            token = self._access_token()
            try:
                return self._request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
                                     mutating=payload is not None, method=method)
            except IntegrationError as error:
                if error.status == 401:
                    self.token, self.token_until = None, 0
                    if attempt == 0:
                        continue  # An explicit authentication rejection did not create a ticket.
                raise
