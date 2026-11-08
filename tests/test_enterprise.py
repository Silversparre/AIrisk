from datetime import timedelta
import io
import json
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select

from airisk.enterprise_api import EnterpriseConfig
from airisk import enterprise_workflow as work
from airisk.models import db, Assessment, AISystem, ExternalLink, Vendor, now
from airisk.services import attention
import test_workflows as workflows

REMOTE_ID = '50000000-0000-0000-0000-000000000001'
SECTION = '50000000-0000-0000-0000-000000000002'
QUESTION = '50000000-0000-0000-0000-000000000003'
CONFIG = {
    'ONETRUST_API_URL': 'https://onetrust.example.test', 'ONETRUST_TOKEN_URL': 'https://onetrust.example.test/token',
    'ONETRUST_CLIENT_ID': 'ot-client', 'ONETRUST_CLIENT_SECRET': 'private-ot-secret', 'ONETRUST_SCOPE': 'ASSESSMENT',
    'ONETRUST_RECORD_URL_TEMPLATE': 'https://onetrust.example.test/assessments/{id}',
    'ONETRUST_TEMPLATE_ID': '50000000-0000-0000-0000-000000000004',
    'ONETRUST_RESPONDENT_ID': '50000000-0000-0000-0000-000000000005', 'ONETRUST_RESPONDENT_NAME': 'Risk team',
    'ONETRUST_SECTION_ID': SECTION, 'ONETRUST_QUESTION_ID': QUESTION,
    'COUPA_API_URL': 'https://coupa.example.test/api', 'COUPA_TOKEN_URL': 'https://coupa.example.test/token',
    'COUPA_CLIENT_ID': 'coupa-client', 'COUPA_CLIENT_SECRET': 'private-coupa-secret', 'COUPA_SCOPE': 'test-scopes',
    'COUPA_RECORD_URL_TEMPLATE': 'https://coupa.example.test/requisition_headers/{id}',
    'COUPA_STATUS_FIELD': 'ai-review-status', 'COUPA_SUMMARY_FIELD': 'ai-review-summary', 'COUPA_URL_FIELD': 'ai-review-url',
    'AIRISK_PUBLIC_URL': 'https://airisk.example.test',
}


class FakeEnterprise:
    """Simulated official JSON contracts exercise real clients, auth and workflows."""
    def __init__(self):
        self.calls, self.assessments = [], {}
        self.mode, self.error = None, None
        self.started, self.release = Event(), Event()
        self.requisition = {'id': 100, 'status': 'pending_approval', 'updated-at': '2026-11-08T12:00:00Z',
            'private-note': 'Do not copy procurement comments',
            'requisition-lines': [{'id': 501, 'description': 'Assistant subscription', 'supplier': {'id': 20}, 'custom-fields': {}},
                                 {'id': 502, 'description': 'Different product', 'supplier': {'id': 21}, 'custom-fields': {}}]}

    def record(self, **extra):
        return {'assessmentId': REMOTE_ID, 'status': 'NOT_STARTED', 'name': 'Existing assessment',
                'totalRiskCount': 4, 'openRiskCount': 2, 'highRisk': 1,
                'sections': [{'sectionId': SECTION, 'questions': [{'question': {'id': QUESTION, 'questionType': 'TEXTBOX'},
                                                                 'questionResponses': []}]}], **extra}

    def answer(self, text):
        entry = self.assessments[REMOTE_ID]['sections'][0]['questions'][0]
        entry['questionResponses'] = [{'responses': [{'type': 'DEFAULT', 'response': text}]}]

    def open(self, request, timeout):
        self.calls.append(request)
        url, method = urlsplit(request.full_url), request.get_method()
        if url.path == '/token':
            form = parse_qs(request.data.decode())
            prefix = 'ONETRUST_' if url.hostname.startswith('onetrust') else 'COUPA_'
            assert form['client_secret'] == [CONFIG[prefix + 'CLIENT_SECRET']]
            assert form['scope'] == [CONFIG[prefix + 'SCOPE']]
            result = {'access_token': 'private-bearer', 'expires_in': 3600}
        else:
            assert request.get_header('Authorization') == 'Bearer private-bearer'
            if self.error:
                raise HTTPError(request.full_url, self.error, 'Remote error', {'Retry-After': '120'},
                                io.BytesIO(b'private-remote-body'))
            if url.path == '/api/assessment/v3/assessments':
                assert method == 'POST'
                if self.mode == 'blocked':
                    self.started.set()
                    assert self.release.wait(10)
                payload = json.loads(request.data)
                assert payload['templateId'] == CONFIG['ONETRUST_TEMPLATE_ID']
                assert payload['respondents'][0]['respondentId'] == CONFIG['ONETRUST_RESPONDENT_ID']
                self.assessments[REMOTE_ID] = self.record(name=payload['name'], description=payload['description'])
                if self.mode == 'launch_timeout':
                    raise URLError('private-network-error after commit')
                result = {'assessmentId': REMOTE_ID}
            elif url.path.endswith('/export'):
                result = self.assessments[url.path.split('/')[-2]]
            elif url.path.endswith('/responses'):
                assert method == 'POST'
                payload = json.loads(request.data)
                assert len(payload) == 1
                assert payload[0]['questionId'] == QUESTION and payload[0]['sectionId'] == SECTION
                assert payload[0]['assessmentId'] == REMOTE_ID
                if self.mode != 'pending':
                    self.answer(payload[0]['responses'][0]['response'])
                if self.mode == 'write_timeout':
                    raise URLError('private-network-error after commit')
                return io.BytesIO(b'')  # OneTrust 202 response has no JSON body.
            elif url.path == '/api/requisitions/100':
                if method == 'PUT':
                    payload = json.loads(request.data)
                    assert set(payload) == {'requisition-lines'}
                    assert len(payload['requisition-lines']) == 1
                    line = payload['requisition-lines'][0]
                    assert set(line) == {'id', 'custom-fields'}
                    assert set(line['custom-fields']) == {CONFIG['COUPA_' + k] for k in ('STATUS_FIELD','SUMMARY_FIELD','URL_FIELD')}
                    target = next(l for l in self.requisition['requisition-lines'] if l['id'] == line['id'])
                    target['custom-fields'].update(line['custom-fields'])
                    if self.mode == 'write_timeout':
                        raise URLError('private-network-error after commit')
                result = self.requisition
            elif url.path.startswith('/api/suppliers/'):
                sid = int(url.path.rsplit('/', 1)[1])
                result = {'id': sid, 'name': 'Vendor One' if sid == 20 else 'Vendor Two', 'tax-id': 'private-tax-id'}
            else:
                raise AssertionError('Unexpected API endpoint: ' + url.path)
        return io.BytesIO(json.dumps(result).encode())

    @property
    def writes(self):
        return [r for r in self.calls if r.get_method() in ('POST', 'PUT') and not r.full_url.endswith('/token')]


class EnterpriseTests(unittest.TestCase):
    tearDown = workflows.WorkflowTests.tearDown
    token = workflows.WorkflowTests.token
    post = workflows.WorkflowTests.post
    login = workflows.WorkflowTests.login
    system = workflows.WorkflowTests.system
    assessment = workflows.WorkflowTests.assessment

    def setUp(self):
        workflows.WorkflowTests.setUp(self)
        self.app.config.update(CONFIG)
        self.remote = FakeEnterprise()
        patcher = patch('airisk.integration_http.build_opener', return_value=self.remote)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.aid = self.assessment()

    def launch(self):
        with self.app.app_context():
            return work.launch(db.session.get(Assessment, self.aid), 1).id

    def intake(self, line=501, complete=True):
        response = self.post('/integrations/coupa/import', {'requisition_id': 100, 'line_id': line})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            link = db.session.scalar(select(ExternalLink).filter_by(provider='coupa', external_id=f'100:{line}'))
            lid, vid = link.id, link.vendor_id
        if complete:
            sid = self.system(coupa_intake=lid, vendor_id=vid)
            self.post(f'/systems/{sid}/assess')
        return lid

    def publish(self, lid, digest=None):
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            assessment = link.assessment or link.system.assessments[0]
            digest = digest or work.summary_for(assessment, link, CONFIG['AIRISK_PUBLIC_URL'])['digest']
            revision = link.revision
        return self.post(f'/integrations/records/{lid}', {'operation': 'publish', 'revision': revision, 'digest': digest})

    def sync(self, lid):
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            link.next_sync_at = now() - timedelta(seconds=1)
            db.session.commit()
            revision = link.revision
        return self.post(f'/integrations/records/{lid}', {'operation': 'sync', 'revision': revision})

    def test_disabled_connections_and_no_secrets_in_ui(self):
        self.app.config['ONETRUST_CLIENT_SECRET'] = ''
        response = self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'launch'})
        self.assertIn(b'not configured', response.data)
        self.assertFalse(self.remote.calls)
        response = self.client.get('/integrations')
        self.assertNotIn(b'private-coupa-secret', response.data)
        self.assertIn(b'COUPA', response.data.upper())

    def test_coupa_intake_is_per_line_idempotent_and_requires_ai_context(self):
        lid = self.intake(complete=False)
        self.assertEqual(self.intake(complete=False), lid)
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            self.assertIsNone(link.system_id)
            self.assertEqual(link.vendor.training_use, 'unknown')
            self.assertEqual(link.source['supplier_id'], 20)
            self.assertNotIn('private', json.dumps(link.source))
            vid = link.vendor_id
        sid = self.system(coupa_intake=lid, vendor_id=vid)
        self.assertIn(str(sid), self.client.get(f'/systems/new?coupa_intake={lid}').location)
        other = self.intake(502)
        with self.app.app_context():
            self.assertEqual(db.session.get(ExternalLink, other).source['supplier_id'], 21)
            self.assertEqual(len(db.session.scalars(select(Vendor)).all()), 2)
        self.assertFalse(self.remote.writes)

    def test_coupa_publishes_only_selected_line_and_reads_back(self):
        lid = self.intake()
        result = self.publish(lid)
        self.assertIn(b'Summary publication confirmed', result.data)
        lines = self.remote.requisition['requisition-lines']
        self.assertEqual(lines[0]['custom-fields']['ai-review-status'], 'review_required')
        self.assertEqual(lines[1]['custom-fields'], {})
        self.assertEqual(self.remote.requisition['status'], 'pending_approval')
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            self.assertEqual(link.system.assessments[0].state, 'draft')
            self.assertEqual(link.publication_state, 'confirmed')

    def test_coupa_changed_supplier_flags_review_and_requires_fresh_preview(self):
        lid = self.intake()
        self.remote.requisition['requisition-lines'][0]['supplier']['id'] = 21
        self.assertIn(b'Coupa changed', self.publish(lid).data)
        self.sync(lid)
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            self.assertEqual(link.vendor.name, 'Vendor One')
            self.assertIn('Coupa supplier or product changed', attention(link.system.assessments[0]))
        self.publish(lid)
        self.assertEqual(self.remote.requisition['requisition-lines'][0]['custom-fields']['ai-review-status'], 'review_required')

    def test_onetrust_launch_is_idempotent_and_progress_is_advisory(self):
        lid = self.launch()
        self.assertEqual(self.launch(), lid)
        self.assertEqual(len(self.remote.writes), 1)
        self.remote.assessments[REMOTE_ID].update(status='COMPLETED', resultName='Approved by OneTrust')
        self.sync(lid)
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            self.assertEqual(link.progress['status'], 'COMPLETED')
            self.assertEqual(link.assessment.state, 'draft')
            self.assertTrue(all(not r.action_done for r in link.assessment.risks))

    def test_onetrust_summary_202_readback_and_duplicate_publish(self):
        lid = self.launch()
        self.assertIn(b'Summary publication confirmed', self.publish(lid).data)
        writes = len(self.remote.writes)
        self.publish(lid)
        self.assertEqual(len(self.remote.writes), writes)
        self.remote.assessments[REMOTE_ID]['status'] = 'COMPLETED'
        self.remote.assessments[REMOTE_ID]['sections'][0]['questions'][0]['lockReason'] = 'Completed'
        self.sync(lid)
        with self.app.app_context():
            self.assertEqual(db.session.get(ExternalLink, lid).publication_state, 'confirmed')
        self.assertIn(b'Publish only while', self.publish(lid).data)

    def test_pending_summary_is_confirmed_by_read_only_sync(self):
        lid = self.launch()
        self.remote.mode = 'pending'
        self.assertIn(b'Summary publication pending', self.publish(lid).data)
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            self.remote.answer(link.publication['text'])
            link.next_sync_at = now()
            db.session.commit()
        before = len(self.remote.writes)
        result = self.app.test_cli_runner().invoke(args=['integrations-sync'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('1 synced', result.output)
        self.assertEqual(len(self.remote.writes), before)
        with self.app.app_context():
            self.assertEqual(db.session.get(ExternalLink, lid).publication_state, 'confirmed')

    def test_ambiguous_launch_is_not_retried_and_recovery_requires_marker(self):
        self.remote.mode = 'launch_timeout'
        response = self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'launch'})
        self.assertNotIn(b'private-network-error', response.data)
        self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'launch'})
        self.assertEqual(len(self.remote.writes), 1)
        with self.app.app_context():
            link = db.session.scalar(select(ExternalLink))
            self.assertEqual(link.state, 'uncertain')
            link.next_sync_at = now()
            db.session.commit()
            lid = link.id
        record = self.remote.assessments[REMOTE_ID]
        saved = record['name']
        record['name'] = 'Unrelated assessment'
        response = self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'link', 'external_id': REMOTE_ID})
        self.assertIn(b'does not contain this recovery reference', response.data)
        record['name'] = saved
        with self.app.app_context():
            db.session.get(ExternalLink, lid).next_sync_at = now()
            db.session.commit()
        response = self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'link', 'external_id': REMOTE_ID})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.session.get(ExternalLink, lid).state, 'linked')
        self.assertEqual(len(self.remote.writes), 1)

    def test_timed_out_publication_recovers_without_write(self):
        for provider in ('onetrust', 'coupa'):
            lid = self.launch() if provider == 'onetrust' else self.intake()
            self.remote.mode = 'write_timeout'
            result = self.publish(lid)
            self.assertNotIn(b'private-network-error', result.data)
            before = len(self.remote.writes)
            self.sync(lid)
            self.assertEqual(len(self.remote.writes), before)
            with self.app.app_context():
                self.assertEqual(db.session.get(ExternalLink, lid).publication_state, 'confirmed')

    def test_remote_human_edits_are_never_overwritten(self):
        lid = self.launch()
        self.remote.answer('Human authored answer')
        self.assertIn(b'contains another answer', self.publish(lid).data)
        self.assertEqual(len(self.remote.writes), 1)
        coupa = self.intake()
        self.publish(coupa)
        self.remote.requisition['requisition-lines'][0]['custom-fields']['ai-review-status'] = 'manual status'
        before = len(self.remote.writes)
        self.assertIn(b'human edits', self.publish(coupa).data)
        self.assertEqual(len(self.remote.writes), before)

    def test_invalid_question_type_and_stale_digest_reject_without_writing(self):
        lid = self.launch()
        before = len(self.remote.calls)
        self.assertIn(b'summary changed', self.publish(lid, '0' * 64).data)
        self.assertEqual(len(self.remote.calls), before)
        self.remote.assessments[REMOTE_ID]['sections'][0]['questions'][0]['question']['questionType'] = 'YESNO'
        self.assertIn(b'TEXTBOX', self.publish(lid).data)
        self.assertEqual(len(self.remote.writes), 1)

    def test_access_csrf_and_stale_revision(self):
        lid = self.launch()
        self.assertEqual(self.client.post(f'/integrations/records/{lid}', data={'operation': 'sync'}).status_code, 400)
        before = len(self.remote.calls)
        result = self.post(f'/integrations/records/{lid}', {'operation': 'sync', 'revision': 999})
        self.assertEqual(result.status_code, 409)
        self.login('member')
        result = self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'launch'})
        self.assertEqual(result.status_code, 403)
        with self.app.app_context():
            revision = db.session.get(ExternalLink, lid).revision
        self.assertEqual(self.post(f'/integrations/records/{lid}', {'operation': 'sync', 'revision': revision}).status_code, 403)
        self.assertEqual(len(self.remote.calls), before)

    def test_instance_and_mapping_changes_cannot_retarget_writes(self):
        lid = self.launch()
        self.publish(lid)
        before = len(self.remote.writes)
        self.app.config['ONETRUST_QUESTION_ID'] = CONFIG['ONETRUST_TEMPLATE_ID']
        self.assertIn(b'original summary field mapping', self.publish(lid).data)
        self.app.config['ONETRUST_API_URL'] = 'https://other.example.test'
        self.assertIn(b'another instance', self.sync(lid).data)
        self.assertEqual(len(self.remote.writes), before)

    def test_malformed_remote_and_rate_limit_keep_progress_and_hide_bodies(self):
        lid = self.launch()
        self.sync(lid)
        self.remote.error = 429
        response = self.sync(lid)
        self.assertNotIn(b'private-remote-body', response.data)
        self.assertIn(b'rate limit', response.data)
        with self.app.app_context():
            link = db.session.get(ExternalLink, lid)
            self.assertEqual(link.progress['status'], 'NOT_STARTED')
            self.assertGreaterEqual((link.next_sync_at - now()).total_seconds(), 119)
        self.remote.error = None
        self.remote.assessments[REMOTE_ID]['assessmentId'] = QUESTION
        self.assertIn(b'unexpected assessment ID', self.sync(lid).data)

    def test_concurrent_launches_create_one_assessment(self):
        self.remote.mode = 'blocked'
        errors = []
        def first():
            try:
                self.launch()
            except Exception as exc:
                errors.append(exc)
        thread = Thread(target=first)
        thread.start()
        try:
            self.assertTrue(self.remote.started.wait(5))
            response = self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'launch'})
            self.assertIn(b'may already have created', response.data)
        finally:
            self.remote.release.set()
            thread.join(10)
        self.assertFalse(errors, errors)
        self.assertEqual(len(self.remote.writes), 1)

    def test_existing_assessment_cannot_link_to_two_local_assessments(self):
        self.remote.assessments[REMOTE_ID] = self.remote.record()
        self.post(f'/assessments/{self.aid}/onetrust', {'operation': 'link', 'external_id': REMOTE_ID})
        another = self.assessment()
        response = self.post(f'/assessments/{another}/onetrust', {'operation': 'link', 'external_id': REMOTE_ID})
        self.assertIn(b'Another operation', response.data)
        with self.app.app_context():
            links = db.session.scalars(select(ExternalLink).where(ExternalLink.external_id == REMOTE_ID)).all()
            self.assertEqual(len(links), 1)
        self.assertFalse(self.remote.writes)

    def test_export_contains_minimal_external_progress(self):
        lid = self.launch()
        self.remote.assessments[REMOTE_ID]['notes'] = ['private note']
        self.sync(lid)
        result = self.client.get(f'/assessments/{self.aid}/export.json')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(b'private note', result.data)
        self.assertIn(b'onetrust', result.data)

    def test_invalid_environment_is_rejected_before_http(self):
        for key, value in [('COUPA_API_URL', 'http://insecure.test/api'),
                           ('COUPA_RECORD_URL_TEMPLATE', 'https://user:secret@example.test/{id}'),
                           ('COUPA_STATUS_FIELD', '../state')]:
            with self.subTest(key=key):
                self.app.config.update(CONFIG)
                self.app.config[key] = value
                with self.app.app_context(), self.assertRaises(work.IntegrationError):
                    EnterpriseConfig.load('coupa')
        self.assertFalse(self.remote.calls)


if __name__ == '__main__':
    unittest.main()
