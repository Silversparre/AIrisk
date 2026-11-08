"""Exercise the same Waitress server on native Windows and Unix."""
import json
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_native_server_preserves_database_and_rejects_occupied_port(self):
        with tempfile.TemporaryDirectory(prefix='airisk local ') as tmp:
            env = {**os.environ, 'AIRISK_INSTANCE': tmp, 'APP_ENV': 'development', 'PYTHONUTF8': '1'}
            env.pop('DATABASE_URL', None)
            env.pop('SECRET_KEY', None)
            migrated = subprocess.run([sys.executable, '-m', 'flask', '--app', 'app', 'db', 'upgrade'],
                                      cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            database = Path(tmp) / 'airisk.db'
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("INSERT INTO user(email,name,password_hash,role,active,session_version) VALUES('retained@example.test','Retained account','unusable-test-hash','member',1,1)")
                connection.commit()
            with socket.socket() as available:
                available.bind(('127.0.0.1', 0))
                port = available.getsockname()[1]
            command = [sys.executable, str(ROOT / 'run_local.py'), '--serve', '--no-browser', '--port', str(port)]
            for restart in range(2):
                with tempfile.TemporaryFile(mode='w+b') as log:
                    # Launch from outside the repository, with a space in the instance path.
                    process = subprocess.Popen(command, cwd=tmp, env=env, stdout=log, stderr=log)
                    try:
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            try:
                                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                                    self.assertEqual(json.load(response), {'status': 'ok'})
                                break
                            except OSError:
                                if process.poll() is not None:
                                    break
                                time.sleep(0.1)
                        else:
                            self.fail('The native local server did not become healthy.')
                        if process.poll() is not None:
                            log.seek(0)
                            self.fail(log.read().decode('utf-8', errors='replace'))
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/login', timeout=3) as response:
                            self.assertIn('Sign in', response.read().decode())
                        if restart == 0:
                            # A different instance tests the socket boundary independently of the data lock.
                            other_env = {**env, 'AIRISK_INSTANCE': str(Path(tmp) / 'another instance')}
                            occupied = subprocess.run(command, cwd=tmp, env=other_env, capture_output=True, text=True, timeout=15)
                            self.assertNotEqual(occupied.returncode, 0)
                            self.assertIn('could not start', occupied.stderr)
                            locked = subprocess.run(command[:-1] + [str(port % 60000 + 1)], cwd=tmp, env=env,
                                                    capture_output=True, text=True, timeout=15)
                            self.assertNotEqual(locked.returncode, 0)
                            self.assertIn('already in use', locked.stderr)
                        with closing(sqlite3.connect(database)) as connection:
                            self.assertEqual(connection.execute('SELECT name FROM user').fetchone()[0], 'Retained account')
                    finally:
                        if os.name == 'nt':
                            # Windows venv python.exe redirects to a child interpreter.
                            # Killing only the redirector leaves the server and DB open.
                            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                           capture_output=True, check=True, timeout=10)
                        else:
                            process.terminate()
                        process.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
