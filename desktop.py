"""Native Windows entry point; the distribution includes its Python runtime."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys

from run_local import port_number

VERSION = '1.2.0'


def data_directory(value=None):
    if value:
        path = Path(value).resolve()
    else:
        base = os.environ.get('LOCALAPPDATA')
        if not base:
            raise RuntimeError('LOCALAPPDATA is unavailable. Specify a private local directory with --instance.')
        path = Path(base).resolve() / 'AIrisk' / 'instance'
    if getattr(sys, 'frozen', False):
        bundle = Path(sys.executable).resolve().parent
        if path == bundle or bundle in path.parents:
            raise RuntimeError('Keep data outside the application folder. Use --instance with a private local directory.')
    return path


def backup(instance, destination):
    target = Path(destination).resolve()
    source = instance / 'airisk.db'
    if not source.is_file():
        raise RuntimeError('No database exists in the selected data directory.')
    with target.open('xb'):
        pass
    try:
        target.chmod(0o600)
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed.')
    except BaseException:
        target.unlink()
        raise
    print(f'Backup verified: {target}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'AIrisk {VERSION}')
    parser.add_argument('--instance', help='Private local data directory; defaults to LOCALAPPDATA/AIrisk/instance.')
    parser.add_argument('--port', type=port_number, default=5000)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--prepare-only', action='store_true', help='Apply migrations and exit.')
    parser.add_argument('command', nargs='?', default='serve', choices=('serve', 'backup', 'admin', 'halo-sync', 'integrations-sync'))
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if os.environ.get('APP_ENV') == 'production' or os.environ.get('DATABASE_URL'):
            raise RuntimeError('The desktop package uses local SQLite. Use the source deployment for hosted operation.')
        instance = data_directory(args.instance or os.environ.get('AIRISK_INSTANCE'))
        if args.command == 'backup':
            if len(args.arguments) != 1:
                parser.error('backup requires one destination filename; existing files are never overwritten.')
            backup(instance, args.arguments[0])
            return 0
        if args.command == 'serve' and args.arguments:
            parser.error('Unexpected arguments after serve.')
        from airisk.runtime import instance_lock, local_app, migrate, serve_application
        from airisk.models import db
        if args.command in ('halo-sync', 'integrations-sync'):
            # Polling can share the running app's DB. It must never run migrations
            # or acquire the exclusive server-startup lock; ticket leases serialize it.
            if not (instance / 'airisk.db').is_file():
                raise RuntimeError('Start AIrisk once before scheduling integration synchronization.')
            app = local_app(instance)
            with app.app_context():
                try:
                    app.cli.main(args=[args.command, *args.arguments], prog_name='AIrisk', standalone_mode=False)
                finally:
                    db.session.remove()
                    db.engine.dispose()
            return 0
        with instance_lock(instance):
            app = local_app(instance)
            migrate(app)
            try:
                if args.command == 'admin':
                    with app.app_context():
                        app.cli.main(args=args.arguments or ['--help'], prog_name='AIrisk admin', standalone_mode=False)
                elif not args.prepare_only:
                    serve_application(app, args.port, not args.no_browser)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f'AIrisk could not start: {exc}', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
