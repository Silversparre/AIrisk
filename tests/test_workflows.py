import json
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from airisk import create_app
from airisk.models import db, User, AISystem, Assessment, Risk, Evidence, Vendor, ActionUpdate
from airisk.services import new_assessment, blockers, rating, stale, attention, action_resolved
from airisk.catalog import triage

PASSWORD = 'a-real-test-password-42'


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, SECRET_KEY='test-secret-' * 4,
                           SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(self.temp.name) / 'test.db'))
        self.app = create_app(self.config)
        with self.app.app_context():
            db.create_all()
            for name, role in [('owner', 'admin'), ('reviewer', 'reviewer'), ('member', 'member')]:
                db.session.add(User(email=name+'@example.test', name=name.title(), role=role, password_hash=generate_password_hash(PASSWORD)))
            db.session.commit()
        self.client = self.app.test_client()
        self.login()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def token(self, path='/'):
        r = self.client.get(path, follow_redirects=True)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        return re.search(r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True)).group(1)

    def post(self, path, data=None, token_path='/'):
        return self.client.post(path, data={'csrf_token': self.token(token_path), **(data or {})}, follow_redirects=True)

    def login(self, who='owner'):
        r = self.post('/login', {'email': who+'@example.test', 'password': PASSWORD}, '/login')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def system(self, **overrides):
        payload = dict(name='Internal policy assistant', purpose='Answer employee questions from approved policies.',
            department='Operations', owner_id='1', model_version='Version 1', ai_type='generative', lifecycle='pilot',
            data_sensitivity='confidential', audience='internal', autonomy='advisory', uses_rag='yes',
            eu_scope='yes', operator_role='deployer', use_domain='general', prohibited='none')
        payload.update(overrides)
        r = self.post('/systems/new', payload)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        with self.app.app_context():
            return db.session.scalar(select(AISystem).order_by(AISystem.id.desc())).id

    def assessment(self, **overrides):
        sid = self.system(**overrides)
        r = self.post(f'/systems/{sid}/assess')
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        with self.app.app_context():
            return db.session.scalar(select(Assessment).order_by(Assessment.id.desc())).id

    def fill_assessment(self, aid, residual=2, action=False):
        r = self.post('/evidence', dict(assessment_id=str(aid), title='Representative evaluation',
            notes='Tested current configuration across representative cases; findings and limits documented.',
            expires_on=str(date.today()+timedelta(days=90)), url='https://example.test/evaluation'))
        self.assertEqual(r.status_code, 200)
        with self.app.app_context():
            eid = db.session.scalar(select(Evidence).filter_by(assessment_id=aid)).id
            risks = [(r.id, r.revision) for r in db.session.get(Assessment, aid).risks]
        for rid, rev in risks:
            data = dict(revision=rev, likelihood='4', impact='4', residual_likelihood=str(residual), residual_impact='4',
                        rationale='Reviewed realistic exposure and affected people; evaluation supports this estimate.',
                        controls='Tested controls in this specific environment.', evidence_ids=str(eid))
            if action:
                data.update(action='Implement additional control and retest.', owner_id='1', due_date=str(date.today()+timedelta(days=14)))
            result = self.post(f'/risks/{rid}', data)
            self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        return eid, risks

    def review(self, aid, eid):
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 200)
        self.login('reviewer')
        self.assertEqual(self.post(f'/evidence/{eid}/verify').status_code, 200)
        self.assertEqual(self.post(f'/assessments/{aid}/legal', dict(legal_review='Reviewed deployment role, Article 5, Article 6 and Article 50. General business support; no Annex III use identified. Literacy and relevant transparency duties recorded.')).status_code, 200)

    def decision(self, aid, state='approved'):
        return self.post(f'/assessments/{aid}/decide', dict(state=state,
            decision='Proceed within the documented purpose and controls. Reassess on changes.',
            review_due=str(date.today()+timedelta(days=60))))

    def test_real_adoption_workflow_persists_and_exports(self):
        aid = self.assessment()
        eid, risks = self.fill_assessment(aid)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid).status_code, 200)
        report = self.client.get(f'/assessments/{aid}/export.json').json
        self.assertEqual(report['state'], 'approved')
        self.assertGreater(len(report['risks']), 10)
        self.assertTrue(all(r['evidence'][0]['current'] for r in report['risks']))
        self.assertEqual(report['approval_blockers'], [])
        self.assertEqual(self.client.get(f'/assessments/{aid}/export.csv').status_code, 200)
        self.assertEqual(self.client.get(f'/assessments/{aid}/export.html').status_code, 200)
        restarted = create_app(self.config)
        with restarted.app_context():
            self.assertEqual(db.session.get(Assessment, aid).state, 'approved')
        cookie = self.client.get_cookie('session')
        contents = self.app.session_interface.get_signing_serializer(self.app).loads(cookie.value)
        self.assertNotIn('assessment_data', contents)
        self.assertNotIn('Internal policy assistant', json.dumps(contents))

    def test_unknown_cannot_be_approved_or_submitted(self):
        aid = self.assessment()
        self.assertEqual(rating(None, None), 'Unassessed')
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 400)
        self.login('reviewer')
        self.assertEqual(self.decision(aid).status_code, 409)

    def test_invalid_scores_are_rejected_not_clamped(self):
        aid = self.assessment()
        with self.app.app_context():
            rid = db.session.get(Assessment, aid).risks[0].id
        for value in ['0', '-1', '6', '999', 'not-a-number']:
            self.assertEqual(self.post(f'/risks/{rid}', dict(revision='1', likelihood=value)).status_code, 400)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Risk, rid).likelihood)

    def test_csrf_is_enforced(self):
        self.assertEqual(self.client.post('/systems/new', data={'name':'bad'}).status_code, 400)
        self.assertEqual(self.client.post('/logout').status_code, 400)

    def test_non_owner_cannot_edit_or_start_assessment(self):
        aid = self.assessment()
        with self.app.app_context():
            a = db.session.get(Assessment, aid)
            sid, rid = a.system_id, a.risks[0].id
        self.login('member')
        self.assertEqual(self.post(f'/systems/{sid}/assess').status_code, 403)
        self.assertEqual(self.post(f'/risks/{rid}', dict(revision=1)).status_code, 403)
        self.assertEqual(self.client.get('/team').status_code, 403)

    def test_self_verification_and_self_approval_blocked(self):
        aid = self.assessment()
        eid, _ = self.fill_assessment(aid)
        self.assertEqual(self.post(f'/evidence/{eid}/verify').status_code, 403)
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 200)
        self.assertEqual(self.decision(aid).status_code, 403)

    def test_changed_context_requires_new_assessment(self):
        aid = self.assessment()
        with self.app.app_context():
            a = db.session.get(Assessment, aid)
            a.system.model_version = 'Version 2'
            db.session.commit()
            self.assertTrue(stale(a))
            self.assertEqual(a.snapshot['model_version'], 'Version 1')
            self.assertTrue(any('context changed' in b for b in blockers(a)))
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 409)

    def test_high_risk_conditional_approval_requires_actions(self):
        aid = self.assessment()
        eid, _ = self.fill_assessment(aid, residual=3, action=True)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid).status_code, 400)
        self.assertEqual(self.decision(aid, 'conditional').status_code, 200)

    def test_prohibited_use_cannot_be_approved(self):
        aid = self.assessment(prohibited='suspected')
        eid, _ = self.fill_assessment(aid)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid, 'conditional').status_code, 400)
        self.assertEqual(self.decision(aid, 'rejected').status_code, 200)

    def test_expired_evidence_blocks_approval(self):
        aid = self.assessment()
        eid, _ = self.fill_assessment(aid)
        self.review(aid, eid)
        with self.app.app_context():
            db.session.get(Evidence, eid).expires_on = date.today()-timedelta(days=1)
            db.session.commit()
        self.assertEqual(self.decision(aid).status_code, 400)

    def test_decisions_are_immutable(self):
        aid = self.assessment()
        eid, risks = self.fill_assessment(aid)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid).status_code, 200)
        self.assertEqual(self.post(f'/assessments/{aid}/reopen', {'reason':'Change history'}).status_code, 409)
        self.login('owner')
        self.assertEqual(self.post(f'/risks/{risks[0][0]}', {'revision':2}).status_code, 409)

    def test_stale_form_cannot_overwrite_risk(self):
        aid = self.assessment()
        with self.app.app_context():
            rid = db.session.get(Assessment, aid).risks[0].id
        self.assertEqual(self.post(f'/risks/{rid}', {'revision':1,'rationale':'First change'}).status_code, 200)
        self.assertEqual(self.post(f'/risks/{rid}', {'revision':1,'rationale':'Lost update'}).status_code, 409)

    def test_hostile_evidence_urls_and_csv_cells(self):
        aid = self.assessment(name='=HYPERLINK("bad")')
        self.assertEqual(self.post('/evidence', dict(assessment_id=aid,title='Bad',notes='Bad',url='javascript:alert(1)',expires_on=str(date.today()+timedelta(days=1)))).status_code,400)
        csv = self.client.get(f'/assessments/{aid}/export.csv').get_data(as_text=True)
        self.assertIn("'=HYPERLINK", csv)

    def test_other_assessment_evidence_cannot_be_attached(self):
        a1, a2 = self.assessment(), self.assessment()
        eid, _ = self.fill_assessment(a1)
        with self.app.app_context():
            rid = db.session.get(Assessment,a2).risks[0].id
        self.assertEqual(self.post(f'/risks/{rid}', {'revision':1,'evidence_ids':eid}).status_code,403)

    def test_predictive_hr_routes_to_high_risk_without_llm_only_risks(self):
        aid = self.assessment(ai_type='predictive', use_domain='employment', uses_rag='')
        with self.app.app_context():
            a = db.session.get(Assessment,aid)
            self.assertEqual(a.snapshot['triage']['label'],'Potential high-risk use')
            keys = [r.definition['key'] for r in a.risks]
            self.assertIn('bias',keys)
            self.assertNotIn('injection',keys)
            self.assertNotIn('retrieval',keys)

    def test_setup_is_disabled_after_first_account_and_in_production(self):
        self.assertEqual(self.client.get('/setup').status_code,302)
        production = create_app({**self.config, 'PRODUCTION':True})
        self.assertEqual(production.test_client().get('/setup').status_code,404)

    def test_logout_and_disabled_account_revoke_access(self):
        self.assertEqual(self.post('/logout').status_code,200)
        self.assertEqual(self.client.get('/').status_code,302)
        self.login('member')
        with self.app.app_context():
            db.session.get(User,3).active=False
            db.session.commit()
        self.assertEqual(self.client.get('/').status_code,302)

    def test_forged_role_and_script_text_do_not_gain_privilege(self):
        sid = self.system(name='<script>alert(1)</script>')
        self.assertNotIn('<script>alert(1)</script>',self.client.get(f'/systems/{sid}').get_data(as_text=True))
        self.login('member')
        self.assertEqual(self.post('/team',dict(role='admin')).status_code,403)

    def test_headers(self):
        r=self.client.get('/')
        self.assertEqual(r.headers['X-Frame-Options'],'DENY')
        self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        self.assertEqual(r.headers['Cache-Control'],'no-store')

    def test_conditional_action_completion_is_independent_and_preserves_decision(self):
        aid = self.assessment()
        eid, risks = self.fill_assessment(aid, residual=3, action=True)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid, 'conditional').status_code, 200)
        rid = risks[0][0]
        self.login('member')
        self.assertEqual(self.post(f'/risks/{rid}/follow-up', {'notes':'Unauthorized'}).status_code, 403)
        self.login('owner')
        self.assertEqual(self.post(f'/risks/{rid}/follow-up', {'notes':'Implemented and retested the permission control.', 'url':'https://example.test/retest'}).status_code, 200)
        with self.app.app_context():
            uid = db.session.scalar(select(ActionUpdate)).id
            self.assertFalse(action_resolved(db.session.get(Risk, rid)))
        self.assertEqual(self.post(f'/action-updates/{uid}/verify').status_code, 403)
        self.login('reviewer')
        self.assertEqual(self.post(f'/action-updates/{uid}/verify').status_code, 200)
        self.assertEqual(self.post(f'/action-updates/{uid}/verify').status_code, 409)
        with self.app.app_context():
            r = db.session.get(Risk, rid)
            self.assertTrue(action_resolved(r))
            self.assertFalse(r.action_done, 'Historical action state is preserved')
            self.assertEqual(r.residual_likelihood, 3)
            self.assertEqual(r.assessment.state, 'conditional')
        payload = self.client.get(f'/assessments/{aid}/export.json').json
        self.assertTrue(payload['risks'][0]['action_resolved_now'])
        self.assertEqual(payload['risks'][0]['follow_up'][0]['verified_by'], 'Reviewer')

    def test_monitoring_exposes_expired_evidence_and_review_dates(self):
        aid = self.assessment()
        eid, _ = self.fill_assessment(aid)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid).status_code, 200)
        with self.app.app_context():
            a = db.session.get(Assessment, aid)
            a.review_due = date.today()
            db.session.get(Evidence, eid).expires_on = date.today()-timedelta(days=1)
            db.session.commit()
            self.assertEqual(attention(a), ['Review due', 'Evidence needs review'])
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('Evidence needs review', page)
        self.assertIn('Review due', page)

    def test_custom_risks_are_assessed_and_locked_with_the_decision(self):
        aid = self.assessment()
        custom = dict(title='Domain-specific AI failure', scenario='Failure found in a representative evaluation.',
            control='Use the validated operational fallback.', expected_evidence='Document the fallback exercise.')
        self.assertEqual(self.post(f'/assessments/{aid}/risks', custom).status_code, 200)
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 400)
        eid, _ = self.fill_assessment(aid)
        self.review(aid, eid)
        self.assertEqual(self.decision(aid).status_code, 200)
        self.login('owner')
        self.assertEqual(self.post(f'/assessments/{aid}/risks', custom).status_code, 409)
        payload = self.client.get(f'/assessments/{aid}/export.json').json
        self.assertEqual(payload['risks'][-1]['definition']['sources'], [])

    def test_rate_limit_and_password_change_revoke_other_sessions(self):
        other = self.app.test_client()
        with self.client.session_transaction() as session:
            saved_session = dict(session)
        with other.session_transaction() as session:
            session.update(saved_session)
        self.assertEqual(self.post('/account', {'current_password':PASSWORD,'password':'new-test-password-42'}).status_code, 200)
        self.assertEqual(other.get('/').status_code, 302)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.post('/logout')
        for _ in range(10):
            self.assertEqual(self.post('/login', {'email':'nobody@example.test','password':'wrong'}, '/login').status_code, 401)
        self.assertEqual(self.post('/login', {'email':'nobody@example.test','password':'wrong'}, '/login').status_code, 429)

    def test_vendor_evidence_reuse_and_changed_terms(self):
        self.assertEqual(self.post('/vendors', dict(name='Vendor',product='Model',training_use='no',
            review_due=str(date.today()+timedelta(days=90)))).status_code, 200)
        with self.app.app_context():
            vid = db.session.scalar(select(Vendor.id))
        aid = self.assessment(vendor_id=vid)
        self.assertEqual(self.post('/evidence', dict(vendor_id=vid,title='Contract',notes='Current signed terms.',
            expires_on=str(date.today()+timedelta(days=90)))).status_code, 200)
        with self.app.app_context():
            rid = db.session.get(Assessment, aid).risks[0].id
            eid = db.session.scalar(select(Evidence.id))
        self.assertEqual(self.post(f'/risks/{rid}', dict(revision=1,evidence_ids=eid)).status_code, 200)
        self.assertEqual(self.post(f'/vendors/{vid}', dict(training_use='yes',review_due=str(date.today()+timedelta(days=90)))).status_code, 200)
        with self.app.app_context():
            self.assertTrue(stale(db.session.get(Assessment, aid)))

    def test_editing_only_evidence_links_invalidates_old_forms(self):
        aid = self.assessment()
        self.assertEqual(self.post('/evidence', dict(assessment_id=aid,title='Evidence',notes='Findings',
            expires_on=str(date.today()+timedelta(days=90)))).status_code, 200)
        with self.app.app_context():
            rid = db.session.get(Assessment,aid).risks[0].id
            eid = db.session.scalar(select(Evidence.id))
        self.assertEqual(self.post(f'/risks/{rid}', dict(revision=1,evidence_ids=eid)).status_code, 200)
        self.assertEqual(self.post(f'/risks/{rid}', dict(revision=1)).status_code, 409)
        with self.app.app_context():
            self.assertEqual([e.id for e in db.session.get(Risk,rid).evidence],[eid])

    def test_administrator_who_edits_answers_cannot_then_approve(self):
        aid = self.assessment()
        eid, risks = self.fill_assessment(aid)
        with self.app.app_context():
            db.session.get(User,2).role='admin'
            db.session.commit()
            rid, rev = risks[0][0], db.session.get(Risk,risks[0][0]).revision
        self.login('reviewer')
        self.assertEqual(self.post(f'/risks/{rid}', dict(revision=rev,not_applicable='yes',rationale='Reviewer changed the risk answer.')).status_code, 200)
        self.login('owner')
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code, 200)
        self.login('reviewer')
        self.assertEqual(self.post(f'/assessments/{aid}/legal', dict(legal_review='Own edited work.')).status_code, 409)
        self.assertEqual(self.decision(aid).status_code, 403)

    def test_return_reason_is_visible_to_author_and_preserved_in_export(self):
        aid = self.assessment()
        eid, _ = self.fill_assessment(aid)
        self.review(aid, eid)
        reason = 'Retest restricted document access; include <script> as a literal adversarial marker.'
        self.assertEqual(self.post(f'/assessments/{aid}/reopen', {'reason':reason}).status_code, 200)
        self.login('owner')
        page = self.client.get(f'/assessments/{aid}').get_data(as_text=True)
        self.assertIn('Changes requested by Reviewer',page)
        self.assertIn('Retest restricted document access;',page)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script> as a literal',page)
        payload = self.client.get(f'/assessments/{aid}/export.json').json
        self.assertEqual(payload['state'],'draft')
        self.assertEqual(payload['legal_review'],'')
        self.assertEqual(payload['history'][-1]['details']['reason'],reason)


if __name__ == '__main__':
    unittest.main()
