import copy
import json
import unittest
from datetime import date, timedelta

from sqlalchemy import select

import test_workflows as workflows
from airisk.claude import PROTOCOL
from airisk.models import db, Assessment, Risk, ClaudeExchange, Evidence


class ClaudeTests(unittest.TestCase):
    # Reuse the HTTP/CSRF fixture without inheriting and rerunning its test cases.
    setUp = workflows.WorkflowTests.setUp
    tearDown = workflows.WorkflowTests.tearDown
    token = workflows.WorkflowTests.token
    post = workflows.WorkflowTests.post
    login = workflows.WorkflowTests.login
    system = workflows.WorkflowTests.system
    assessment = workflows.WorkflowTests.assessment
    fill_assessment = workflows.WorkflowTests.fill_assessment

    def prepare(self, aid, mode='draft', include=False, count=1):
        with self.app.app_context():
            risks = [r.id for r in db.session.get(Assessment, aid).risks[:count]]
        response = self.post(f'/assessments/{aid}/claude', dict(mode=mode, workspace='Claude Enterprise',
            risk_ids=[str(rid) for rid in risks], include_evidence='yes' if include else ''))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        with self.app.app_context():
            return db.session.scalar(select(ClaudeExchange.id).order_by(ClaudeExchange.id.desc()))

    def response_for(self, eid):
        with self.app.app_context():
            e = db.session.get(ClaudeExchange, eid)
            result = dict(protocol=PROTOCOL, exchange_id=e.id, assessment_id=e.assessment_id,
                context_fingerprint=e.context_digest, mode=e.mode,
                summary='Synthetic fixture: uncertainties require a human review.', risks=[])
            for r in e.request_context['risks']:
                result['risks'].append(dict(risk_id=r['id'], rationale='Synthetic proposed reasoning, subject to evidence review.',
                    likelihood=3, impact=4, residual_likelihood=None, residual_impact=None,
                    suggested_controls='Proposed only: test the human fallback.', evidence_gaps='No validated effectiveness evidence.',
                    questions='Who owns the fallback?', evidence_ids=[]))
            return result

    def receive(self, eid, payload=None):
        return self.post(f'/claude/{eid}/import', dict(response=json.dumps(payload or self.response_for(eid)), model_label='Synthetic test fixture'))

    def test_prepare_scopes_context_and_omits_evidence_by_default(self):
        aid = self.assessment()
        with self.app.app_context():
            a = db.session.get(Assessment,aid)
            a.risks[1].rationale='OTHER_RISK_PRIVATE_MARKER'
            db.session.add(Evidence(assessment_id=aid,title='Private evidence',notes='EVIDENCE_PRIVATE_MARKER',
                added_by=1,expires_on=date.today()+timedelta(days=30)))
            db.session.commit()
        eid = self.prepare(aid)
        prompt = self.client.get(f'/claude/{eid}/prompt.txt').get_data(as_text=True)
        self.assertNotIn('EVIDENCE_PRIVATE_MARKER',prompt)
        self.assertNotIn('OTHER_RISK_PRIVATE_MARKER',prompt)
        self.assertIn(PROTOCOL,prompt)
        self.assertIn('untrusted data',prompt)
        with self.app.app_context():
            e = db.session.get(ClaudeExchange,eid)
            self.assertEqual(e.workspace,'Claude Enterprise')
            self.assertEqual(len(e.request_context['risks']),1)
            self.assertEqual(e.request_context['evidence'],[])

    def test_import_requires_explicit_acceptance_and_only_updates_selected_fields(self):
        aid = self.assessment()
        eid = self.prepare(aid,count=2)
        payload = self.response_for(eid)
        rid, other = [r['risk_id'] for r in payload['risks']]
        self.assertEqual(self.receive(eid,payload).status_code,200)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Risk,rid).likelihood)
        self.assertEqual(self.post(f'/claude/{eid}/accept',dict(risk_ids=str(rid))).status_code,400)
        self.assertEqual(self.post(f'/claude/{eid}/accept',dict(risk_ids=str(rid),confirmed='yes')).status_code,200)
        with self.app.app_context():
            r = db.session.get(Risk,rid)
            self.assertEqual((r.likelihood,r.impact),(3,4))
            self.assertIsNone(r.residual_likelihood)
            self.assertEqual(r.controls,'')
            self.assertEqual(r.evidence,[])
            self.assertEqual(r.assessment.state,'draft')
            self.assertIsNone(r.assessment.reviewer_id)
            self.assertIsNone(db.session.get(Risk,other).likelihood)
        self.assertEqual(self.post(f'/claude/{eid}/accept',dict(risk_ids=str(rid),confirmed='yes')).status_code,409)
        self.assertEqual(self.post(f'/risks/{rid}',dict(revision=1,rationale='stale tab')).status_code,409)
        exported = self.client.get(f'/assessments/{aid}/export.json').json
        self.assertEqual(exported['claude_assistance'][0]['accepted_risk_ids'],[rid])
        self.assertEqual(exported['claude_assistance'][0]['prepared_by'],'Owner')

    def test_rejects_forged_identifiers_extra_fields_and_invalid_ratings(self):
        aid = self.assessment()
        eid = self.prepare(aid)
        valid = self.response_for(eid)
        variants=[]
        for field,value in [('assessment_id',999),('exchange_id',True),('context_fingerprint','forged'),('state','approved')]:
            bad=copy.deepcopy(valid); bad[field]=value; variants.append(bad)
        for value in [0,6,True,1.5,'3']:
            bad=copy.deepcopy(valid); bad['risks'][0]['likelihood']=value; variants.append(bad)
        bad=copy.deepcopy(valid); bad['risks'][0]['risk_id']=999; variants.append(bad)
        bad=copy.deepcopy(valid); bad['risks'][0]['evidence_ids']=[999]; variants.append(bad)
        bad=copy.deepcopy(valid); bad['risks'][0]['residual_likelihood']=1; variants.append(bad)
        bad=copy.deepcopy(valid); bad['risks'][0]['rationale']='x'*4001; variants.append(bad)
        bad=copy.deepcopy(valid); bad['summary']='Invalid \ud800 text'; variants.append(bad)
        bad=copy.deepcopy(valid); bad['risks'][0]['rationale']='Invalid \udfff text'; variants.append(bad)
        for payload in variants:
            self.assertEqual(self.receive(eid,payload).status_code,400)
        self.assertEqual(self.post(f'/claude/{eid}/import',dict(response='{"protocol":1,"protocol":2}')).status_code,400)
        self.assertEqual(self.post(f'/claude/{eid}/import',dict(response='not json')).status_code,400)
        with self.app.app_context():
            self.assertIsNone(db.session.get(ClaudeExchange,eid).response)

    def test_context_changes_block_import_and_acceptance(self):
        aid = self.assessment()
        eid = self.prepare(aid)
        payload = self.response_for(eid)
        self.assertEqual(self.receive(eid,payload).status_code,200)
        rid = payload['risks'][0]['risk_id']
        self.assertEqual(self.post(f'/risks/{rid}',dict(revision=1,rationale='New human finding')).status_code,200)
        self.assertEqual(self.post(f'/claude/{eid}/accept',dict(risk_ids=str(rid),confirmed='yes')).status_code,409)
        next_id = self.prepare(aid)
        with self.app.app_context():
            a=db.session.get(Assessment,aid)
            a.system.model_version='New deployment'
            db.session.commit()
        self.assertEqual(self.receive(next_id).status_code,409)

    def test_evidence_changes_invalidate_even_an_evidence_omitted_request(self):
        aid = self.assessment()
        eid = self.prepare(aid)
        self.assertEqual(self.post('/evidence',dict(assessment_id=aid,title='New results',notes='New failure observed.',
            expires_on=str(date.today()+timedelta(days=30)))).status_code,200)
        self.assertEqual(self.receive(eid).status_code,409)

    def test_review_advice_cannot_change_risks_or_make_a_decision(self):
        aid = self.assessment()
        self.fill_assessment(aid)
        self.assertEqual(self.post(f'/assessments/{aid}/submit').status_code,200)
        with self.app.app_context():
            rid=db.session.get(Assessment,aid).risks[0].id
        self.assertEqual(self.post(f'/assessments/{aid}/claude',dict(mode='review',workspace='Claude.ai',risk_ids=rid)).status_code,409)
        self.login('reviewer')
        eid = self.prepare(aid,mode='review',include=True)
        self.assertEqual(self.receive(eid).status_code,200)
        self.assertEqual(self.post(f'/claude/{eid}/accept',dict(confirmed='yes',risk_ids=rid)).status_code,403)
        with self.app.app_context():
            a=db.session.get(Assessment,aid)
            self.assertEqual(a.state,'in_review')
            self.assertEqual(a.legal_review,'')
            self.assertEqual(a.risks[0].likelihood,4)
            self.assertTrue(all(e.verified_at is None for e in a.evidence))

    def test_permissions_csrf_and_import_replay(self):
        aid = self.assessment()
        eid = self.prepare(aid)
        payload = self.response_for(eid)
        self.login('member')
        self.assertEqual(self.post(f'/assessments/{aid}/claude',dict(mode='draft',workspace='Claude.ai',risk_ids=payload['risks'][0]['risk_id'])).status_code,403)
        self.assertEqual(self.receive(eid,payload).status_code,403)
        self.login('owner')
        self.assertEqual(self.client.post(f'/claude/{eid}/import',data={'response':json.dumps(payload)}).status_code,400)
        self.assertEqual(self.receive(eid,payload).status_code,200)
        self.assertEqual(self.receive(eid,payload).status_code,409)

    def test_malformed_risk_selection_returns_validation_error(self):
        aid=self.assessment()
        for value in ('²','-1','1.5','9'*50):
            self.assertEqual(self.post(f'/assessments/{aid}/claude',dict(mode='draft',workspace='Claude.ai',risk_ids=value)).status_code,400)

    def test_untrusted_response_is_escaped_and_code_fences_are_accepted(self):
        aid = self.assessment()
        eid = self.prepare(aid)
        payload = self.response_for(eid)
        payload['summary']='<script>alert("unsafe")</script>'
        response=self.post(f'/claude/{eid}/import',dict(response='```json\n'+json.dumps(payload)+'\n```'))
        self.assertEqual(response.status_code,200)
        page=response.get_data(as_text=True)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>alert(',page)

    def test_not_applicable_risk_requires_human_reconsideration(self):
        aid=self.assessment()
        with self.app.app_context():
            r=db.session.get(Assessment,aid).risks[0]
            r.not_applicable=True; r.rationale='Human exclusion with reasoning.'
            db.session.commit()
        eid=self.prepare(aid)
        payload=self.response_for(eid)
        self.assertEqual(self.receive(eid,payload).status_code,200)
        self.assertEqual(self.post(f'/claude/{eid}/accept',dict(confirmed='yes',risk_ids=payload['risks'][0]['risk_id'])).status_code,400)


if __name__ == '__main__':
    unittest.main()
