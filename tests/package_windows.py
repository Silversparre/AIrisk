"""Opt-in native Windows package acceptance; uses a disposable database only."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Run this acceptance test on native Windows.')
    executable = str(args.exe.resolve())
    node = str(args.node.resolve())
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='AIrisk packaged acceptance ') as temp:
        folder = Path(temp)
        instance = folder / 'private data'
        env = {**os.environ, 'PATH': os.environ['SystemRoot'] + '\\System32',
               'AIRISK_SETUP_TOKEN': secrets.token_urlsafe(32), 'PYTHONUTF8': '1',
               'AIRISK_TEST_URL': f'http://127.0.0.1:{port}'}
        for key in ('AIRISK_INSTANCE', 'DATABASE_URL', 'SECRET_KEY', 'PYTHONPATH', 'PYTHONHOME', 'APP_ENV'):
            env.pop(key, None)
        command = [executable, '--instance', str(instance), '--port', str(port), '--no-browser']
        before = None
        for restart in range(2):
            with (folder / f'server-{restart}.log').open('w+b') as log:
                server = subprocess.Popen(command, cwd=folder, env=env, stdout=log, stderr=log)
                try:
                    for _ in range(120):
                        try:
                            with urllib.request.urlopen(env['AIRISK_TEST_URL'] + '/health', timeout=1) as response:
                                assert json.load(response) == {'status': 'ok'}
                            break
                        except OSError:
                            if server.poll() is not None:
                                log.seek(0)
                                raise AssertionError(log.read().decode('utf-8', errors='replace'))
                            time.sleep(.25)
                    else:
                        raise AssertionError('Packaged server did not become healthy.')
                    hostile = urllib.request.Request(env['AIRISK_TEST_URL'] + '/login', headers={'Host': 'attacker.example'})
                    try:
                        urllib.request.urlopen(hostile, timeout=3)
                        raise AssertionError('Host boundary accepted a hostile hostname.')
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 400
                    if restart == 0:
                        duplicate = subprocess.run(command, cwd=folder, env=env, capture_output=True, text=True, timeout=20)
                        assert duplicate.returncode != 0 and 'already in use' in duplicate.stderr, duplicate.stderr
                        try:
                            urllib.request.urlopen(env['AIRISK_TEST_URL'] + '/setup', timeout=3)
                            raise AssertionError('Setup accepted a missing private nonce.')
                        except urllib.error.HTTPError as exc:
                            assert exc.code == 403
                        subprocess.run([node, str(ROOT / 'tests/browser.cjs')], cwd=ROOT, env=env, check=True, timeout=180)
                        backup = folder / 'verified backup.db'
                        backup_command = [executable, '--instance', str(instance), 'backup', str(backup)]
                        subprocess.run(backup_command, cwd=folder, env=env, check=True, timeout=20)
                        refused = subprocess.run(backup_command, cwd=folder, env=env, capture_output=True, timeout=20)
                        assert refused.returncode != 0, 'Existing backup was overwritten.'
                        with closing(sqlite3.connect(backup)) as saved:
                            assert saved.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                    with closing(sqlite3.connect(instance / 'airisk.db')) as database:
                        counts = {name: database.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                                  for name in ('user', 'ai_system', 'assessment', 'evidence', 'audit_event', 'claude_exchange')}
                        if before is None:
                            before = counts
                            assert counts['user'] == 2 and counts['assessment'] >= 2, counts
                        else:
                            assert counts == before, (before, counts)
                except BaseException:
                    log.flush()
                    log.seek(0)
                    print(log.read().decode('utf-8', errors='replace'), file=sys.stderr)
                    raise
                finally:
                    if server.poll() is None:
                        subprocess.run(['taskkill', '/PID', str(server.pid), '/T', '/F'], capture_output=True, check=True, timeout=10)
                    server.wait(timeout=10)
        maintained = subprocess.run([executable, '--instance', str(instance), 'admin', '--help'],
                                    cwd=folder, env=env, capture_output=True, text=True, timeout=20)
        assert maintained.returncode == 0 and 'reset-password' in maintained.stdout, maintained.stderr
        # Test data cannot live inside the distributed application tree.
        forbidden = subprocess.run([executable, '--instance', str(args.exe.resolve().parent / 'data'), '--prepare-only'],
                                   cwd=folder, env=env, capture_output=True, text=True, timeout=20)
        assert forbidden.returncode != 0 and 'outside the application folder' in forbidden.stderr
        print(json.dumps({'platform': sys.platform, 'python_on_runtime_path': False,
                          'restart_preserved': before, 'backup_integrity': 'ok', 'package_acceptance': 'passed'}))


if __name__ == '__main__':
    main()
