from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import re
from threading import Barrier
import unittest
from unittest.mock import patch

from sqlalchemy import delete, func, select
from werkzeug.security import generate_password_hash

from airisk.models import AuthSession, LoginAttempt, SetupClaim, User, db, now

import test_workflows as workflows


class SecurityTests(unittest.TestCase):
    setUp = workflows.WorkflowTests.setUp
    tearDown = workflows.WorkflowTests.tearDown
    token = workflows.WorkflowTests.token
    post = workflows.WorkflowTests.post
    login = workflows.WorkflowTests.login
    system = workflows.WorkflowTests.system
    assessment = workflows.WorkflowTests.assessment
    fill_assessment = workflows.WorkflowTests.fill_assessment

    def test_unknown_host_is_rejected_before_serving_pages(self):
        response = self.client.get('/login', headers={'Host': 'rebind.attacker.example'})
        self.assertEqual(response.status_code, 400)

    def test_logout_revokes_a_copied_cookie(self):
        stolen = self.client.get_cookie('session').value
        self.assertEqual(self.post('/logout').status_code, 200)
        replay = self.app.test_client()
        replay.set_cookie('session', stolen)
        self.assertEqual(replay.get('/').status_code, 302)

    def test_cross_origin_post_is_rejected_even_with_a_valid_form_token(self):
        response = self.client.post('/logout', data={'csrf_token': self.token()},
                                    headers={'Origin': 'https://attacker.example'})
        self.assertEqual(response.status_code, 403)

    def test_malformed_evidence_identifier_is_a_validation_error(self):
        aid = self.assessment()
        from airisk.models import db, Assessment
        with self.app.app_context():
            risk_id = db.session.get(Assessment, aid).risks[0].id
        for value in ('²', '9' * 100):
            response = self.post(f'/risks/{risk_id}', {'revision': '1', 'evidence_ids': value})
            self.assertEqual(response.status_code, 400)

    def test_huge_route_identifiers_do_not_reach_the_database(self):
        self.assertEqual(self.client.get('/assessments/' + '9' * 80).status_code, 404)

    def test_expired_server_session_cannot_be_extended_by_cookie_replay(self):
        with self.app.app_context():
            auth = db.session.scalar(select(AuthSession))
            auth.expires_at = now() - timedelta(seconds=1)
            db.session.commit()
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_login_limits_cover_rotating_names_and_ignore_forwarded_ip(self):
        self.app.config.update(LOGIN_SOURCE_LIMIT=3, LOGIN_GLOBAL_LIMIT=20)
        with self.app.app_context():
            db.session.execute(delete(LoginAttempt)); db.session.commit()
        for number in range(4):
            response = self.client.post('/login', data={'csrf_token': self.token('/login'),
                'email': f'unknown{number}@example.test', 'password': 'incorrect'},
                headers={'X-Forwarded-For': f'198.51.100.{number+1}'})
            self.assertEqual(response.status_code, 401 if number < 3 else 429)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(LoginAttempt)), 5)

    def test_parallel_logins_cannot_overrun_the_account_budget(self):
        self.app.config.update(LOGIN_ACCOUNT_LIMIT=3)
        with self.app.app_context():
            db.session.execute(delete(LoginAttempt)); db.session.commit()

        def attempt(_):
            client = self.app.test_client()
            page = client.get('/login').get_data(as_text=True)
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
            return client.post('/login', data={'csrf_token': csrf,
                'email': 'target@example.test', 'password': 'incorrect'}).status_code

        with patch('airisk.web.check_password_hash', return_value=False), ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(attempt, range(12)))
        self.assertEqual(codes.count(401), 3, codes)
        self.assertEqual(codes.count(429), 9, codes)

    def empty_workspace(self):
        self.app.config['SETUP_TOKEN'] = 'private-test-setup-token-' * 3
        with self.app.app_context():
            db.session.remove(); db.drop_all(); db.create_all()
        self.client = self.app.test_client()

    def setup_client(self):
        client = self.app.test_client()
        response = client.get('/setup?token=' + self.app.config['SETUP_TOKEN'], follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)
        return client, csrf

    def test_first_account_needs_the_private_launcher_link(self):
        self.empty_workspace()
        self.assertEqual(self.client.get('/setup').status_code, 403)
        self.assertEqual(self.client.get('/setup?token=guessed').status_code, 403)
        client, csrf = self.setup_client()
        self.assertEqual(client.post('/setup', data={'csrf_token': csrf, 'email': 'first@example.test',
            'name': 'First admin', 'password': 'a-long-test-password'}).status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(User)), 1)
            self.assertIsNotNone(db.session.get(SetupClaim, 1))

    def test_concurrent_setup_creates_only_one_administrator(self):
        self.empty_workspace()
        clients = [self.setup_client(), self.setup_client()]
        barrier = Barrier(2)

        def concurrent_hash(password):
            barrier.wait(timeout=10)
            return generate_password_hash(password)

        def submit(index):
            client, csrf = clients[index]
            return client.post('/setup', data={'csrf_token': csrf, 'email': f'first{index}@example.test',
                'name': 'First admin', 'password': 'a-long-test-password'}).status_code

        with patch('airisk.web.generate_password_hash', side_effect=concurrent_hash), ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(codes), [302, 409])
        with self.app.app_context():
            self.assertEqual(db.session.scalar(select(func.count()).select_from(User)), 1)

    def test_author_cannot_return_their_own_assessment_from_review(self):
        aid = self.assessment()
        self.fill_assessment(aid)
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 200)
        self.assertEqual(self.post(f'/assessments/{aid}/reopen', {'reason': 'Clear the independent review'}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
