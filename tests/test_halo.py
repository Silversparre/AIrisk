"""Connector contract and workflow tests using a deterministic Halo HTTP transport."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import io
import json
from threading import Event
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select, update

import test_workflows as workflows
from airisk.halo import HaloClient, HaloConfig, HaloError, NoRedirects, MAX_RESPONSE
from airisk.halo_workflow import HaloBusy, create_ticket, sync_ticket
from airisk.models import db, ActionUpdate, Assessment, AuditEvent, HaloTicket, Risk, now
from airisk.services import action_resolved, attention

CONFIG = {'HALO_API_URL': 'https://halo.example.test/api',
          'HALO_TOKEN_URL': 'https://auth.example.test/token?tenant=pilot',
          'HALO_CLIENT_ID': 'test-client', 'HALO_CLIENT_SECRET': 'secret-never-render-this',
          'HALO_TICKET_URL_TEMPLATE': 'https://halo.example.test/ticket?id={id}',
          'HALO_TICKET_TYPE_ID': '17', 'HALO_TEAM': 'AI Operations', 'HALO_AGENT_ID': '22',
          'HALO_SITE_ID': '5', 'HALO_COMPLETED_STATUS_IDS': '9,19',
          'AIRISK_PUBLIC_URL': 'https://airisk.example.test'}


class FakeHalo:
    """Exercise the real OAuth, JSON, validation and workflow code without real credentials."""
    def __init__(self):
        self.calls, self.tickets = [], {}
        self.create_mode = None
        self.get_error = None
        self.note = {'id': 4, 'ticket_id': 700, 'datetime': '2026-11-08T12:00:00Z',
                     'note': 'Implementation is in progress.', 'hiddenfromuser': False}
        self.started, self.release = Event(), Event()

    def open(self, request, timeout):
        self.calls.append(request)
        url = urlsplit(request.full_url)
        if url.path == '/token':
            form = parse_qs(request.data.decode())
            assert form['grant_type'] == ['client_credentials']
            assert form['scope'] == ['read:tickets edit:tickets']
            assert form['client_secret'] == [CONFIG['HALO_CLIENT_SECRET']]
            assert parse_qs(url.query)['tenant'] == ['pilot']
            result = {'access_token': 'private-access-token', 'expires_in': 3600}
        else:
            assert request.get_header('Authorization') == 'Bearer private-access-token'
            if url.path == '/api/Tickets' and request.get_method() == 'POST':
                if self.create_mode == 'reject':
                    raise HTTPError(request.full_url, 400, 'Bad request', {}, io.BytesIO(b'private-remote-error'))
                if self.create_mode == 'blocked':
                    self.started.set()
                    assert self.release.wait(10)
                payload = json.loads(request.data)
                assert len(payload) == 1
                ticket_id = 700 + len(self.tickets)
                result = {**payload[0], 'id': ticket_id, 'status_id': 1, 'status_name': 'Open',
                          'agent_name': 'Halo implementer', 'last_update': '2026-11-08T12:00:00Z'}
                self.tickets[ticket_id] = result
                if self.create_mode == 'timeout':
                    raise URLError('secret-never-render-this: timeout after commit')
            elif url.path.startswith('/api/Tickets/'):
                if self.get_error:
                    raise HTTPError(request.full_url, self.get_error, 'Remote error',
                                    {'Retry-After': '120'}, io.BytesIO(b'secret-never-render-this'))
                result = self.tickets[int(url.path.rsplit('/', 1)[-1])]
            elif url.path == '/api/Actions':
                query = parse_qs(url.query)
                assert query['excludeprivate'] == ['true']
                assert query['includehtmlnote'] == ['false']
                result = {'actions': [dict(self.note, ticket_id=int(query['ticket_id'][0]))] if self.note else []}
            else:
                raise AssertionError('Unexpected API resource')
        return io.BytesIO(json.dumps(result).encode())

    @property
    def creations(self):
        return [c for c in self.calls if c.get_method() == 'POST' and urlsplit(c.full_url).path == '/api/Tickets']


class HaloTests(unittest.TestCase):
    tearDown = workflows.WorkflowTests.tearDown
    token = workflows.WorkflowTests.token
    post = workflows.WorkflowTests.post
    login = workflows.WorkflowTests.login
    system = workflows.WorkflowTests.system
    assessment = workflows.WorkflowTests.assessment

    def setUp(self):
        workflows.WorkflowTests.setUp(self)
        self.app.config.update(CONFIG)
        self.remote = FakeHalo()
        self.transport = patch('airisk.halo.build_opener', return_value=self.remote)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        self.aid = self.assessment()
        with self.app.app_context():
            a = db.session.get(Assessment, self.aid)
            r = a.risks[0]
            r.action, r.owner_id = 'Test retrieval access and document the results.', 1
            r.due_date = date.today() + timedelta(days=14)
            r.rationale = 'PRIVATE_ASSESSMENT_REASONING'
            a.legal_review = 'PRIVATE_DECISION_TEXT'
            db.session.commit()
            self.rid, self.revision = r.id, r.revision

    def send(self):
        return self.post(f'/risks/{self.rid}/halo/create', {'revision': self.revision})

    def link(self):
        return db.session.scalar(select(HaloTicket).where(HaloTicket.risk_id == self.rid))

    def test_create_sync_export_and_duplicate_click(self):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('Halo ticket #700', body)
        self.assertIn('Implementation is in progress.', body)
        payload = json.loads(self.remote.creations[0].data)[0]
        self.assertEqual((payload['tickettype_id'], payload['team'], payload['agent_id'], payload['site_id']),
                         (17, 'AI Operations', 22, 5))
        self.assertIn('owner@example.test', payload['details'])
        self.assertIn(f'https://airisk.example.test/assessments/{self.aid}#risk-{self.rid}', payload['details'])
        self.assertNotIn('PRIVATE_ASSESSMENT_REASONING', payload['details'])
        self.assertNotIn('PRIVATE_DECISION_TEXT', payload['details'])
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(len(self.remote.creations), 1)
        report = self.client.get(f'/assessments/{self.aid}/export.json').json
        external = report['risks'][0]['halo_ticket']
        self.assertEqual(external['ticket_id'], 700)
        self.assertEqual(external['progress']['status'], 'Open')
        self.assertIn('HaloITSM progress', self.client.get(f'/assessments/{self.aid}/export.html').get_data(as_text=True))
        self.assertNotIn(CONFIG['HALO_CLIENT_SECRET'], json.dumps(report))

    def test_closed_ticket_requires_human_evidence_and_independent_verification(self):
        with self.app.app_context():
            a = db.session.get(Assessment, self.aid)
            a.state, a.decided_at, a.reviewer_id = 'conditional', now(), 2
            a.decision = 'Proceed with the documented conditions.'
            a.risks[0].residual_likelihood, a.risks[0].residual_impact = 3, 4
            db.session.commit()
            self.revision = a.risks[0].revision
            before = (a.state, a.revision, a.decision, a.decided_at, a.risks[0].revision)
        self.send()
        self.remote.tickets[700].update(status_id=9, status_name='Resolved')
        response = self.post(f'/risks/{self.rid}/halo/sync')
        self.assertIn('Halo reports completion', response.get_data(as_text=True))
        with self.app.app_context():
            r = db.session.get(Risk, self.rid)
            a = r.assessment
            self.assertEqual((a.state, a.revision, a.decision, a.decided_at, r.revision), before)
            self.assertFalse(action_resolved(r))
            self.assertEqual(db.session.scalar(select(func.count()).select_from(ActionUpdate)), 0)
            self.assertEqual((r.residual_likelihood, r.residual_impact), (3, 4))
        self.post(f'/risks/{self.rid}/follow-up', {'notes': 'Reviewed actual test results in Halo.',
                  'url': 'https://halo.example.test/ticket?id=700'})
        with self.app.app_context():
            update_id = db.session.scalar(select(ActionUpdate.id))
        self.assertEqual(self.post(f'/action-updates/{update_id}/verify').status_code, 403)
        self.login('reviewer')
        self.assertEqual(self.post(f'/action-updates/{update_id}/verify').status_code, 200)
        self.remote.tickets[700].update(status_id=2, status_name='In progress')
        self.post(f'/risks/{self.rid}/halo/sync')
        with self.app.app_context():
            r = db.session.get(Risk, self.rid)
            self.assertTrue(action_resolved(r))  # Keep the historical verification.
            self.assertIn('Halo remediation reopened', attention(r.assessment))
            self.assertTrue(r.halo_ticket.reopened)
            self.assertEqual(r.assessment.state, 'conditional')

    def test_timeout_never_reposts_and_matching_ticket_can_be_recovered(self):
        self.remote.create_mode = 'timeout'
        self.assertEqual(self.send().status_code, 200)
        with self.app.app_context():
            self.assertEqual(self.link().state, 'uncertain')
            self.assertIsNone(self.link().ticket_id)
            self.assertNotIn(CONFIG['HALO_CLIENT_SECRET'], self.link().last_error)
        self.send()
        self.assertEqual(len(self.remote.creations), 1)
        response = self.post(f'/risks/{self.rid}/halo/recover', {'ticket_id': 700})
        self.assertIn('Halo ticket #700 recovered', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual((self.link().state, self.link().ticket_id), ('linked', 700))
        self.assertEqual(len(self.remote.creations), 1)

    def test_wrong_ticket_cannot_be_recovered_and_reset_requires_absence_confirmation(self):
        self.remote.create_mode = 'timeout'
        self.send()
        self.remote.tickets[700]['summary'] = 'Unrelated ticket'
        self.remote.tickets[700]['details'] = 'Different action'
        response = self.post(f'/risks/{self.rid}/halo/recover', {'ticket_id': 700})
        self.assertIn('does not contain this action', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertIsNone(self.link().ticket_id)
        self.assertEqual(self.post(f'/risks/{self.rid}/halo/reset').status_code, 400)
        self.assertEqual(self.post(f'/risks/{self.rid}/halo/reset', {'confirmed_absent': 'yes'}).status_code, 200)
        with self.app.app_context():
            self.assertEqual(self.link().state, 'ready')

    def test_rejected_creation_can_retry_after_backoff(self):
        self.remote.create_mode = 'reject'
        self.send()
        with self.app.app_context():
            self.assertEqual(self.link().state, 'ready')
        self.send()
        self.assertEqual(len(self.remote.creations), 1)
        with self.app.app_context():
            self.link().next_sync_at = now() - timedelta(seconds=1)
            db.session.commit()
        self.remote.create_mode = None
        self.send()
        self.assertEqual(len(self.remote.creations), 2)
        self.assertEqual(len(self.remote.tickets), 1)

    def test_sync_failure_preserves_progress_and_honors_rate_limit(self):
        self.send()
        with self.app.app_context():
            before = self.link().progress
            synced = self.link().last_synced_at
        self.remote.get_error = 429
        self.post(f'/risks/{self.rid}/halo/sync')
        with self.app.app_context():
            link = self.link()
            self.assertEqual(link.progress, before)
            self.assertEqual(link.last_synced_at, synced)
            self.assertGreater(link.next_sync_at, now() + timedelta(seconds=110))
            self.assertIsNone(link.lease_token)
            self.assertNotIn(CONFIG['HALO_CLIENT_SECRET'], link.last_error)
        calls = len(self.remote.calls)
        self.post(f'/risks/{self.rid}/halo/sync')
        self.assertEqual(len(self.remote.calls), calls)

    def test_permissions_csrf_and_stale_forms(self):
        path = f'/risks/{self.rid}/halo/create'
        self.assertEqual(self.client.post(path, data={'revision': self.revision}).status_code, 400)
        self.assertEqual(self.post(path, {'revision': self.revision-1}).status_code, 409)
        self.login('member')
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(self.post(f'/risks/{self.rid}/halo/sync').status_code, 403)
        self.assertEqual(self.client.get('/integrations/halo').status_code, 403)
        self.assertEqual(self.remote.calls, [])

    def test_disabled_configuration_does_not_make_network_calls(self):
        self.app.config['HALO_CLIENT_SECRET'] = ''
        page = self.client.get(f'/assessments/{self.aid}').get_data(as_text=True)
        self.assertIn('disabled>Create Halo ticket', page)
        self.send()
        self.assertEqual(self.remote.calls, [])
        result = self.app.test_cli_runner().invoke(args=['halo-sync'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('not configured', result.output)
        with self.app.app_context():
            self.assertIsNone(self.link())

    def test_sync_does_not_assume_closed_status_or_import_private_notes(self):
        self.app.config['HALO_COMPLETED_STATUS_IDS'] = ''
        self.send()
        self.remote.tickets[700].update(status_id=9, status_name='Cancelled', dateclosed='2026-11-08T12:05:00Z')
        self.remote.note['hiddenfromuser'] = True
        self.remote.note['note'] = 'PRIVATE_HALO_NOTE'
        response = self.post(f'/risks/{self.rid}/halo/sync')
        self.assertNotIn('PRIVATE_HALO_NOTE', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertFalse(self.link().progress['completion_reported'])
            self.assertIsNone(self.link().progress['public_note'])

    def test_remote_html_is_text_and_same_snapshot_does_not_duplicate_history(self):
        self.remote.note['note'] = '<p>Done &amp; tested</p><script>remote_script()</script>'
        self.remote.tickets.clear()
        response = self.send()
        body = response.get_data(as_text=True)
        self.assertIn('Done &amp; tested', body)
        self.assertNotIn('remote_script()', body)
        with self.app.app_context():
            count = db.session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'halo progress updated'))
        self.post(f'/risks/{self.rid}/halo/sync')
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'halo progress updated')), count)

    def test_changed_action_is_flagged_without_overwriting_halo_or_local_owner(self):
        self.send()
        with self.app.app_context():
            r = db.session.get(Risk, self.rid)
            r.action = 'New action details after ticket creation.'
            r.owner_id = 3
            db.session.commit()
        self.post(f'/risks/{self.rid}/halo/sync')
        with self.app.app_context():
            r = db.session.get(Risk, self.rid)
            self.assertIn('Halo action details changed', attention(r.assessment))
            self.assertEqual(r.owner_id, 3)
        self.assertEqual(len(self.remote.creations), 1)

    def test_instance_change_does_not_access_same_id_on_another_server(self):
        self.send()
        calls = len(self.remote.calls)
        self.app.config['HALO_API_URL'] = 'https://other.example.test/api'
        response = self.post(f'/risks/{self.rid}/halo/sync')
        self.assertIn('different Halo instance', response.get_data(as_text=True))
        self.assertEqual(len(self.remote.calls), calls)

    def test_scheduled_sync_only_polls_due_links_and_never_creates_tickets(self):
        self.send()
        runner = self.app.test_cli_runner()
        calls = len(self.remote.calls)
        result = runner.invoke(args=['halo-sync'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('0 synced', result.output)
        self.assertEqual(len(self.remote.calls), calls)
        with self.app.app_context():
            self.link().next_sync_at = now() - timedelta(seconds=1)
            db.session.commit()
        self.remote.tickets[700].update(status_id=2, status_name='In progress')
        result = runner.invoke(args=['halo-sync', '--limit', '1'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('1 synced', result.output)
        self.assertEqual(len(self.remote.creations), 1)
        with self.app.app_context():
            self.assertEqual(self.link().progress['status'], 'In progress')

    def test_concurrent_create_has_only_one_remote_side_effect(self):
        self.remote.create_mode = 'blocked'

        def send_in_context():
            with self.app.app_context():
                try:
                    return create_ticket(db.session.get(Risk, self.rid), 1)[0].ticket_id
                except HaloError:
                    return None
                finally:
                    db.session.remove()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(send_in_context)
            self.assertTrue(self.remote.started.wait(10))
            second = pool.submit(send_in_context)
            self.assertIsNone(second.result(timeout=10))
            self.remote.release.set()
            self.assertEqual(first.result(timeout=10), 700)
        self.assertEqual(len(self.remote.creations), 1)

    def test_live_sync_lease_and_abandoned_create_are_not_replayed(self):
        self.send()
        with self.app.app_context():
            link = self.link()
            link.lease_token, link.lease_until = 'other-worker', now() + timedelta(minutes=1)
            db.session.commit()
            calls = len(self.remote.calls)
            with self.assertRaises(HaloBusy):
                sync_ticket(link)
            self.assertEqual(len(self.remote.calls), calls)
            link.lease_token, link.lease_until = None, now() - timedelta(minutes=1)
            link.state, link.ticket_id = 'creating', None
            db.session.commit()
            with self.assertRaises(HaloError):
                create_ticket(db.session.get(Risk, self.rid), 1)
            self.assertEqual(len(self.remote.calls), calls)

    def test_configuration_rejects_unsafe_urls_without_exposing_secrets(self):
        with self.app.app_context():
            for key, bad in [('HALO_API_URL', 'http://halo.example.test/api'),
                             ('HALO_TOKEN_URL', 'https://user:password@halo.example.test/token'),
                             ('HALO_TICKET_URL_TEMPLATE', 'javascript:alert({id})'),
                             ('HALO_TICKET_TYPE_ID', True),
                             ('HALO_COMPLETED_STATUS_IDS', '9,-1')]:
                with self.subTest(key=key), patch.dict(self.app.config, {key: bad}):
                    with self.assertRaises(HaloError) as error:
                        HaloConfig.load()
                    self.assertNotIn('password@', str(error.exception))
            self.assertNotIn(CONFIG['HALO_CLIENT_SECRET'], repr(HaloConfig.load()))
        body = self.client.get('/integrations/halo').get_data(as_text=True)
        self.assertIn('Configuration is present', body)
        self.assertNotIn(CONFIG['HALO_CLIENT_SECRET'], body)

    def test_transport_rejects_redirects_oversized_and_invalid_creation_responses(self):
        self.assertIsNone(NoRedirects().redirect_request(None, None, 302, '', {}, 'https://untrusted.test'))
        with self.app.app_context():
            client = HaloClient(HaloConfig.load())
            client.token, client.token_until = 'token', float('inf')
            with patch.object(self.remote, 'open', return_value=io.BytesIO(b'x' * (MAX_RESPONSE + 1))):
                with self.assertRaises(HaloError) as error:
                    client.create_ticket({'summary': 'test'})
                self.assertTrue(error.exception.ambiguous)
            with patch.object(self.remote, 'open', return_value=io.BytesIO(b'{"id": true}')):
                with self.assertRaises(HaloError) as error:
                    client.create_ticket({'summary': 'test'})
                self.assertTrue(error.exception.ambiguous)

    def test_expired_bearer_is_refreshed_once_after_explicit_401(self):
        original = self.remote.open
        rejected = False

        def expired_once(request, timeout):
            nonlocal rejected
            if urlsplit(request.full_url).path == '/api/Tickets' and not rejected:
                rejected = True
                raise HTTPError(request.full_url, 401, 'Expired', {}, io.BytesIO(b'private-auth-error'))
            return original(request, timeout)

        with self.app.app_context(), patch.object(self.remote, 'open', side_effect=expired_once):
            link, created = create_ticket(db.session.get(Risk, self.rid), 1)
            self.assertTrue(created)
            self.assertEqual(link.ticket_id, 700)
        self.assertEqual(len(self.remote.tickets), 1)
        self.assertEqual(len([c for c in self.remote.calls if urlsplit(c.full_url).path == '/token']), 2)

    def test_malformed_remote_snapshot_preserves_last_successful_progress(self):
        self.send()
        self.remote.tickets[700]['id'] = 999
        response = self.post(f'/risks/{self.rid}/halo/sync')
        self.assertIn('unexpected ID or missing status', response.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(self.link().progress['status'], 'Open')
            self.assertEqual(self.link().ticket_id, 700)

    def test_batch_continues_after_one_ticket_fails(self):
        self.send()
        with self.app.app_context():
            a = db.session.get(Assessment, self.aid)
            r = a.risks[1]
            r.action, r.owner_id, r.due_date = 'Another control test', 1, date.today()+timedelta(days=7)
            db.session.commit()
            other, _ = create_ticket(r, 1)
            other_id = other.id
            self.link().next_sync_at = now()-timedelta(seconds=1)
            db.session.commit()
        self.remote.tickets[700]['id'] = 999
        result = self.app.test_cli_runner().invoke(args=['halo-sync'])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('1 synced, 0 busy, 1 failed', result.output)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(HaloTicket, other_id).last_synced_at)

    def test_replaced_lease_cannot_commit_an_older_snapshot(self):
        self.send()
        self.remote.tickets[700].update(status_id=2, status_name='Old worker result')
        with self.app.app_context():
            link = self.link()
            newest = dict(link.progress, status='New worker result')

            def newer_worker_claims_before_commit(*args):
                # A replacement worker claims an expired lease after the old worker's
                # final read, but before its commit. The old snapshot must not win.
                with db.engine.begin() as connection:
                    connection.execute(update(HaloTicket).where(HaloTicket.id == link.id).values(
                        revision=HaloTicket.revision+1, lease_token='replacement-worker', progress=newest))

            with patch('airisk.halo_workflow.audit', side_effect=newer_worker_claims_before_commit):
                with self.assertRaises(HaloBusy):
                    sync_ticket(link)
            db.session.expire_all()
            self.assertEqual(self.link().progress['status'], 'New worker result')


if __name__ == '__main__':
    unittest.main()
