"""Local runtime shared by the source launcher and the Windows executable."""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import socket
from urllib.parse import urlencode
import webbrowser

from flask_migrate import upgrade
from sqlalchemy import select

from . import create_app
from .models import User, db


def local_app(instance=None):
    if instance is not None:
        os.environ['AIRISK_INSTANCE'] = str(Path(instance).resolve())
    setup_token = os.environ.get('AIRISK_SETUP_TOKEN') or secrets.token_urlsafe(32)
    if len(setup_token) < 32:
        raise RuntimeError('AIRISK_SETUP_TOKEN must contain at least 32 characters.')
    return create_app({'SETUP_TOKEN': setup_token,
                       'TRUSTED_HOSTS': ['localhost', '127.0.0.1', '[::1]']})


@contextmanager
def instance_lock(instance):
    """Keep local startup/migrations and serving exclusive to one data directory."""
    path = Path(instance)
    path.mkdir(parents=True, exist_ok=True)
    with (path / 'server.lock').open('a+b') as handle:
        # Windows denies reads of a region locked by another process. Inspect
        # the file size without touching that region before acquiring the lock.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('This data directory is already in use. Close its AIrisk server before starting another.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def migrate(app):
    with app.app_context():
        upgrade()


def serve_application(app, port, open_browser):
    from waitress import create_server

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
        server = create_server(app, sockets=[listener], threads=4,
                               max_request_body_size=app.config['MAX_CONTENT_LENGTH'])
    except BaseException:
        listener.close()
        raise
    base = f'http://127.0.0.1:{port}/'
    with app.app_context():
        first_run = not db.session.scalar(select(User.id).limit(1))
    url = base + 'setup?' + urlencode({'token': app.config['SETUP_TOKEN']}) if first_run else base
    print(f'AIrisk is running at {base}\nData: {app.instance_path}\nKeep this window open. Stop with Ctrl+C.', flush=True)
    if first_run:
        print(f'Private first-time setup link (keep it to yourself): {url}', flush=True)
    try:
        if open_browser:
            try:
                webbrowser.open(url)
            except (webbrowser.Error, OSError):
                print('Open the address shown above in your browser.', flush=True)
        server.run()
    finally:
        server.close()
        server.task_dispatcher.shutdown()
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
