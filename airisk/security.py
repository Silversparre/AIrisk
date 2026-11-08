"""Request boundaries and revocable authentication for the SQLite workspace."""
import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from flask import abort, current_app, request, session
from sqlalchemy import case, delete, update
from sqlalchemy.dialects.sqlite import insert
from werkzeug.routing import BaseConverter

from .models import AuthSession, LoginAttempt, db, now


class RecordIDConverter(BaseConverter):
    regex = r'[0-9]{1,18}'

    def to_python(self, value):
        return int(value)

    def to_url(self, value):
        return str(int(value))


def record_id(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 18 or int(value) < 1:
        abort(400, description='Choose a valid record identifier.')
    return int(value)


def request_boundary():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        abort(403, description='Submit this form from AIrisk itself.')
    origin = request.headers.get('Origin')
    if origin:
        try:
            parsed = urlsplit(origin)
            scheme = 'https' if current_app.config['PRODUCTION'] else request.scheme
            valid = (parsed.scheme == scheme and parsed.netloc.lower() == request.host.lower()
                     and not parsed.path and not parsed.query and not parsed.fragment
                     and not parsed.username and not parsed.password)
        except ValueError:
            valid = False
        if not valid:
            abort(403, description='Submit this form from AIrisk itself.')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def current_session():
    token = session.get('auth_token')
    if not isinstance(token, str) or len(token) != 64 or not token.isascii():
        return None
    stored = db.session.get(AuthSession, digest(token))
    if stored and stored.user_id == session.get('user_id') and stored.expires_at > now():
        return stored
    return None


def revoke_current_session():
    token = session.get('auth_token')
    if isinstance(token, str) and len(token) == 64 and token.isascii():
        db.session.execute(delete(AuthSession).where(AuthSession.token_hash == digest(token)))


def start_session(user):
    revoke_current_session()
    db.session.execute(delete(AuthSession).where(AuthSession.expires_at <= now()))
    token = secrets.token_hex(32)
    db.session.add(AuthSession(token_hash=digest(token), user_id=user.id,
                              expires_at=now() + current_app.permanent_session_lifetime))
    session.clear()
    session.update(user_id=user.id, session_version=user.session_version, auth_token=token)
    session.permanent = True


def account_bucket(email):
    return digest('account:' + email)


def consume_login_budget(email):
    """Reserve attempts atomically before password hashing; count rotating names too."""
    timestamp = now()
    window = timedelta(minutes=15)
    db.session.execute(delete(LoginAttempt).where(LoginAttempt.window_start < timestamp - 2 * window))
    buckets = [('global', 'workspace', current_app.config['LOGIN_GLOBAL_LIMIT']),
               ('source', request.remote_addr or 'unknown', current_app.config['LOGIN_SOURCE_LIMIT']),
               ('account', email, current_app.config['LOGIN_ACCOUNT_LIMIT'])]
    for kind, value, limit in buckets:
        key = digest(kind + ':' + value)
        fresh = LoginAttempt.window_start <= timestamp - window
        statement = insert(LoginAttempt).values(key=key, count=1, window_start=timestamp)
        statement = statement.on_conflict_do_update(index_elements=[LoginAttempt.key], set_={
            'count': case((fresh, 1), else_=LoginAttempt.count + 1),
            'window_start': case((fresh, timestamp), else_=LoginAttempt.window_start),
        }).returning(LoginAttempt.count)
        count = db.session.scalar(statement)
        # Commit the reservation even when this request will be rejected.
        db.session.commit()
        if count > limit:
            abort(429, description='Too many sign-in attempts. Try again in 15 minutes.')


def reset_account_budget(email):
    db.session.execute(update(LoginAttempt).where(LoginAttempt.key == account_bucket(email)).values(count=0))


def setup_access():
    token = current_app.config.get('SETUP_TOKEN')
    if not token or len(token) < 32:
        return False
    saved = session.get('setup_access', '')
    return isinstance(saved, str) and hmac.compare_digest(saved, digest(token))


def accept_setup_link():
    expected = current_app.config.get('SETUP_TOKEN')
    candidate = request.args.get('token', '')
    if expected and len(expected) >= 32 and candidate and hmac.compare_digest(candidate.encode(), expected.encode()):
        session['setup_access'] = digest(expected)
        return True
    return False
