import os
import secrets
from pathlib import Path
from datetime import timedelta
from flask import Flask, render_template
from sqlalchemy.orm.exc import StaleDataError
from werkzeug.exceptions import Conflict, SecurityError
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from .models import db

csrf = CSRFProtect()


@event.listens_for(Engine, 'connect')
def sqlite_constraints(connection, _):
    if connection.__class__.__module__.startswith('sqlite3'):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')


def create_app(config=None):
    root = Path(__file__).resolve().parent.parent
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'),
                instance_path=os.environ.get('AIRISK_INSTANCE', str(root / 'instance')))
    production = os.environ.get('APP_ENV', 'development') == 'production'
    app.config.update(SECRET_KEY=os.environ.get('SECRET_KEY'),
        SQLALCHEMY_DATABASE_URI=os.environ.get('DATABASE_URL', 'sqlite:///airisk.db'),
        SQLALCHEMY_TRACK_MODIFICATIONS=False, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=production, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=128 * 1024,
        WTF_CSRF_TIME_LIMIT=8 * 60 * 60, PRODUCTION=production,
        TRUSTED_HOSTS=[host.strip() for host in os.environ.get('AIRISK_TRUSTED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',') if host.strip()],
        SETUP_TOKEN=os.environ.get('AIRISK_SETUP_TOKEN'),
        LOGIN_ACCOUNT_LIMIT=10, LOGIN_SOURCE_LIMIT=50, LOGIN_GLOBAL_LIMIT=200,
        MAX_FORM_MEMORY_SIZE=128 * 1024, MAX_FORM_PARTS=100)
    if config:
        app.config.update(config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if not app.config['SECRET_KEY']:
        if production:
            raise RuntimeError('SECRET_KEY is required in production. Use a random value of at least 32 characters.')
        key_file = Path(app.instance_path) / 'secret.key'
        if not key_file.exists():
            try:
                with key_file.open('x') as f:
                    f.write(secrets.token_hex(32))
                key_file.chmod(0o600)
            except FileExistsError:
                pass
        app.config['SECRET_KEY'] = key_file.read_text().strip()
    if len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('SECRET_KEY must contain at least 32 characters.')
    db.init_app(app)
    Migrate(app, db, directory=str(root / 'migrations'))
    from .security import RecordIDConverter, request_boundary
    app.url_map.converters['int'] = RecordIDConverter
    app.before_request(request_boundary)
    csrf.init_app(app)
    from .web import bp
    from .claude import bp as claude_bp
    from .cli import register_commands
    app.register_blueprint(bp)
    app.register_blueprint(claude_bp)
    register_commands(app)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        # A global no-referrer policy can make browser form POST origins "null".
        # Keep normal same-origin forms compatible with the Origin boundary;
        # the private bootstrap redirect itself must not propagate its token.
        from flask import request
        response.headers['Referrer-Policy'] = 'no-referrer' if request.path == '/setup' and request.args.get('token') else 'same-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store'
        if app.config['PRODUCTION']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    for status in (400, 403, 404, 409, 413, 429):
        app.register_error_handler(status, lambda e: (render_template('error.html', error=e), e.code))

    @app.errorhandler(SecurityError)
    def untrusted_host(_):
        # Host rejection occurs before Flask has an adapter for template URLs.
        return 'Untrusted request host.', 400, {'Content-Type': 'text/plain; charset=utf-8'}

    @app.errorhandler(StaleDataError)
    def concurrent_edit(_):
        db.session.rollback()
        return render_template('error.html', error=Conflict('This record changed while you were saving. Reload and review the latest version.')), 409
    return app
