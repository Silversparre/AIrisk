"""Reproduce findings on the ORIGINAL checkout, commit 74b6823 (not the new app).

Usage: python review/reproduce_review.py /absolute/path/to/baseline-checkout
The application executes in a disposable copy and writes no user records.
"""
import base64
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zlib

if len(sys.argv) != 2:
    raise SystemExit('Pass the path to a checkout of baseline commit 74b6823ed6a702dffa2c6b572d5d5206bfb5e39f.')
repo = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
os.environ.pop('SECRET_KEY', None)
results = {}
with tempfile.TemporaryDirectory(prefix='airisk-review-') as tmp:
    root = Path(tmp)
    for name in ('app.py', 'models.py', 'risk_logic.py'):
        shutil.copy2(repo / name, root / name)
    for name in ('templates', 'static'):
        shutil.copytree(repo / name, root / name)
    sys.path.insert(0, str(root))
    app_module = importlib.import_module('app')
    app, db = app_module.app, app_module.db
    from models import Assessment, RiskEntry
    from risk_logic import calculate_risk_details
    client = app.test_client()

    def token_for(client, step):
        page = client.get(f'/assessment/step/{step}')
        return re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)

    empty = client.get('/assessment/result')
    results['empty_result'] = {'status': empty.status_code, 'very_low': '<span class="value">Very Low</span>' in empty.get_data(as_text=True)}
    results['empty_calculation'] = calculate_risk_details({})['risk_level']
    results['invalid_step_statuses'] = {str(i): client.get(f'/assessment/step/{i}').status_code for i in (0, 11, 999)}
    results['missing_csrf_status'] = client.post('/assessment/step/1', data={'threat_source': 'test'}).status_code
    skip = client.post('/assessment/step/10', data={'csrf_token': token_for(client, 10)}, follow_redirects=True)
    results['skip_to_end'] = {'status': skip.status_code, 'very_low': '<span class="value">Very Low</span>' in skip.get_data(as_text=True)}

    client.get('/assessment/start')
    steps = [
        {'threat_source': 'SYNTHETIC_VENDOR_RISK'},
        {'threat_event': 'Sensitive AI output'},
        {'capability': 'High'}, {'intent': 'High'}, {'targeting': 'High'},
        {'likelihood_initiation': '4'}, {'vulnerability': 'Excessive data access'},
        {'likelihood_impact': '4'}, {'impact_level': '4'}, {},
    ]
    statuses = []
    for step, data in enumerate(steps, 1):
        statuses.append(client.post(f'/assessment/step/{step}', data={**data, 'csrf_token': token_for(client, step)}).status_code)
    complete = client.get('/assessment/result')
    with app.app_context():
        counts = {'assessments': Assessment.query.count(), 'risk_entries': RiskEntry.query.count()}
    results['complete_assessment'] = {'post_statuses': statuses, 'result_status': complete.status_code, 'high': '<span class="value">High</span>' in complete.get_data(as_text=True), 'database_rows': counts}
    results['back_navigation_prefills_value'] = 'value="SYNTHETIC_VENDOR_RISK"' in client.get('/assessment/step/1').get_data(as_text=True)
    results['review_step_displays_answers'] = 'SYNTHETIC_VENDOR_RISK' in client.get('/assessment/step/10').get_data(as_text=True)

    cookie = client.get_cookie('session').value
    payload = cookie.split('.')[1 if cookie.startswith('.') else 0]
    raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
    decoded = json.loads(zlib.decompress(raw) if cookie.startswith('.') else raw)
    results['cookie_readable_without_signing_key'] = decoded['assessment_data']['threat_source'] == 'SYNTHETIC_VENDOR_RISK'
    results['cookie_secure'] = client.get_cookie('session').secure
    results['cookie_httponly'] = client.get_cookie('session').http_only
    results['default_key_active'] = app.config['SECRET_KEY'] == 'dev-secret-key-change-in-prod'

    invalid = client.post('/assessment/step/10', data={'csrf_token': token_for(client, 10), 'arbitrary_field': 'accepted', 'impact_level': '999'}, follow_redirects=True)
    with client.session_transaction() as session:
        accepted = session['assessment_data'].get('arbitrary_field') == 'accepted'
    results['arbitrary_fields_and_invalid_score'] = {'status': invalid.status_code, 'unknown_impact': '<strong>Impact Level:</strong> Unknown' in invalid.get_data(as_text=True), 'very_high_risk': '<span class="value">Very High</span>' in invalid.get_data(as_text=True), 'arbitrary_field_accepted': accepted}
    results['negative_scores'] = calculate_risk_details({'likelihood_initiation': '-10', 'likelihood_impact': '-10', 'impact_level': '-10'})
    reset = client.get('/assessment/start', follow_redirects=True)
    with client.session_transaction() as session:
        results['start_get_discards_assessment'] = session['assessment_data'] == {}
    with app.app_context():
        db.session.remove()
        db.engine.dispose()

print(json.dumps(results, indent=2))
