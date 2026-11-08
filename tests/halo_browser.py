"""Run a real browser against disposable AIrisk data and simulated Halo responses."""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask_migrate import upgrade
from sqlalchemy import select
from waitress import create_server
from werkzeug.security import generate_password_hash

from airisk import create_app
from airisk.cli import seed_examples
from airisk.models import db, Assessment, User, now
from test_halo import CONFIG, FakeHalo
from test_enterprise import CONFIG as ENTERPRISE_CONFIG, FakeEnterprise, REMOTE_ID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--browser', help='Optional installed Chromium/Edge executable.')
    parser.add_argument('--baseline', action='store_true', help='Run the existing general browser journey instead.')
    parser.add_argument('--enterprise', action='store_true', help='Run the OneTrust and Coupa browser journey.')
    args = parser.parse_args()
    if not args.node:
        parser.error('Provide an installed Node executable with --node.')
    with tempfile.TemporaryDirectory(prefix='airisk-halo-browser-') as temporary:
        folder = Path(temporary)
        control = folder / 'halo-control.json'
        control.write_text('{}')
        password, setup_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with patch.dict(os.environ, {'AIRISK_INSTANCE': temporary, 'APP_ENV': 'development'}):
            app = create_app({**CONFIG, **(ENTERPRISE_CONFIG if args.enterprise else {}),
                'SECRET_KEY': secrets.token_hex(32), 'SETUP_TOKEN': setup_token,
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(folder / 'browser.db'),
                'HALO_CLIENT_SECRET': '' if args.baseline else CONFIG['HALO_CLIENT_SECRET']})
        with app.app_context():
            upgrade()
            if not args.baseline:
                for name, role in [('owner', 'admin'), ('reviewer', 'reviewer')]:
                    db.session.add(User(name=name.title(), email=f'{name}@halo-qa.test', role=role,
                                        password_hash=generate_password_hash(password)))
                db.session.flush()
                seed_examples(db.session.get(User, 1))
                assessment = db.session.scalar(select(Assessment).order_by(Assessment.id))
                # Synthetic conditional record; the main browser journey tests the full decision flow.
                assessment.state, assessment.reviewer_id, assessment.decided_at = 'conditional', 2, now()
                assessment.decision = 'Synthetic fixture: proceed subject to the documented evaluation action.'
                assessment.review_due = date.today() + timedelta(days=60)
                db.session.commit()
                aid, rid = assessment.id, assessment.risks[0].id

        class BrowserHalo(FakeHalo):
            def open(self, request, timeout):
                if urlsplit(request.full_url).path.startswith('/api/Tickets/') and 700 in self.tickets:
                    self.tickets[700].update(json.loads(control.read_text()))
                return super().open(request, timeout)

        remote = BrowserHalo()
        class BrowserEnterprise(FakeEnterprise):
            def open(self, request, timeout):
                changes = json.loads(control.read_text())
                if REMOTE_ID in self.assessments and changes.get('onetrust_status'):
                    self.assessments[REMOTE_ID]['status'] = changes['onetrust_status']
                if changes.get('coupa_supplier'):
                    self.requisition['requisition-lines'][0]['supplier']['id'] = changes['coupa_supplier']
                return super().open(request, timeout)
        enterprise = BrowserEnterprise()
        server = create_server(app, host='127.0.0.1', port=0, threads=4)
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        env = {k: v for k, v in os.environ.items() if not k.startswith('HALO_')}
        env.update(AIRISK_TEST_URL=f'http://127.0.0.1:{server.effective_port}', AIRISK_SETUP_TOKEN=setup_token)
        if args.browser:
            env['AIRISK_BROWSER'] = args.browser
        if not args.baseline:
            env.update(AIRISK_HALO_PASSWORD=password, AIRISK_HALO_CONTROL=str(control),
                       AIRISK_HALO_ASSESSMENT=str(aid), AIRISK_HALO_RISK=str(rid))
        try:
            with patch('airisk.halo.build_opener', return_value=remote), patch(
                    'airisk.integration_http.build_opener', return_value=enterprise):
                result = subprocess.run([args.node, str(ROOT / 'tests' / (
                    'browser.cjs' if args.baseline else ('browser_enterprise.cjs' if args.enterprise else 'browser_halo.cjs')))],
                    cwd=ROOT, env=env, timeout=180)
            if args.enterprise and result.returncode == 0:
                assert len(enterprise.writes) == 3, 'Exactly one launch and two summary writes are expected.'
            elif not args.baseline and result.returncode == 0:
                assert len(remote.creations) == 1, 'Browser journey must create exactly one remote ticket.'
            return result.returncode
        finally:
            server.close()
            server.task_dispatcher.shutdown()
            thread.join(timeout=5)
            with app.app_context():
                db.session.remove()
                db.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
