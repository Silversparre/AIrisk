from copy import deepcopy
from datetime import date
from sqlalchemy import select
from .models import db, Assessment, Risk, AuditEvent
from .catalog import VERSION, REVIEW_BY, SOURCES, RISKS, applicable, triage

FIELDS = ('name', 'purpose', 'department', 'owner_id', 'vendor_id', 'model_version', 'ai_type',
          'lifecycle', 'data_sensitivity', 'audience', 'autonomy', 'uses_rag', 'eu_scope',
          'operator_role', 'use_domain', 'prohibited', 'context_notes')
TERMINAL = ('approved', 'conditional', 'rejected')


def context(system):
    result = {field: getattr(system, field) for field in FIELDS}
    if system.vendor:
        result['vendor'] = {field: getattr(system.vendor, field) for field in
                            ('name', 'product', 'training_use', 'retention', 'regions', 'notes')}
        result['vendor']['review_due'] = str(system.vendor.review_due) if system.vendor.review_due else None
    return result


def audit(actor, entity, entity_id, action, details=None):
    db.session.add(AuditEvent(actor_id=actor, entity=entity, entity_id=entity_id,
                             action=action, details=details or {}))


def new_assessment(system, author):
    snapshot = context(system)
    snapshot['sources'] = deepcopy(SOURCES)
    snapshot['catalog_review_by'] = REVIEW_BY
    snapshot['triage'] = triage(snapshot)
    assessment = Assessment(system=system, author_id=author, catalog_version=VERSION, snapshot=snapshot)
    db.session.add(assessment)
    db.session.flush()
    for definition in RISKS:
        if applicable(definition, snapshot):
            assessment.risks.append(Risk(definition=deepcopy(definition)))
    audit(author, 'assessment', assessment.id, 'created', {'catalog_version': VERSION})
    return assessment


def rating(likelihood, impact):
    if likelihood is None or impact is None:
        return 'Unassessed'
    score = likelihood * impact
    return 'Critical' if score >= 20 else 'High' if score >= 12 else 'Moderate' if score >= 6 else 'Low'


def stale(assessment):
    return any(assessment.snapshot.get(k) != v for k, v in context(assessment.system).items())


def evidence_current(evidence):
    return bool(evidence.verified_at and evidence.expires_on >= date.today())


def risk_complete(risk):
    if risk.not_applicable:
        return bool(risk.rationale.strip())
    return all(getattr(risk, field) is not None for field in
               ('likelihood', 'impact', 'residual_likelihood', 'residual_impact')) and bool(risk.rationale.strip() and risk.controls.strip())


def action_resolved(risk):
    return risk.action_done or any(u.verified_at for u in risk.action_updates)


def attention(assessment):
    """Current monitoring signals, separate from the historical decision."""
    signals = []
    if stale(assessment):
        signals.append('Context changed')
    if assessment.review_due and assessment.review_due <= date.today():
        signals.append('Review due')
    vendor = assessment.system.vendor
    if vendor and (not vendor.review_due or vendor.review_due <= date.today()):
        signals.append('Vendor review due')
    if assessment.state in ('approved', 'conditional'):
        if any(not r.not_applicable and not any(evidence_current(e) for e in r.evidence) for r in assessment.risks):
            signals.append('Evidence needs review')
        if any(r.action and not action_resolved(r) and r.due_date and r.due_date < date.today() for r in assessment.risks):
            signals.append('Action overdue')
    if date.today() > date.fromisoformat(REVIEW_BY):
        signals.append('Framework review due')
    return signals


def blockers(assessment, conditional=False):
    issues = []
    if stale(assessment):
        issues.append('System or vendor context changed. Create a new assessment from the current context.')
    if date.today() > date.fromisoformat(REVIEW_BY):
        issues.append('The framework catalog is due for editorial review before new approvals.')
    if assessment.review_due and assessment.review_due <= date.today():
        issues.append('The decision is due for reassessment.')
    vendor = assessment.system.vendor
    if vendor and (not vendor.review_due or vendor.review_due <= date.today()):
        issues.append('Vendor due diligence is due for review.')
    snap = assessment.snapshot
    if snap['prohibited'] == 'suspected':
        issues.append('A possible prohibited practice must be resolved in the system context before approval.')
    if 'unknown' in (snap['prohibited'], snap['eu_scope'], snap['operator_role']):
        issues.append('EU scope, prohibited-practice screening or operator role remains unknown.')
    if not assessment.legal_review or not assessment.legal_reviewer_id:
        issues.append('An independent reviewer must record the legal applicability review.')
    for r in assessment.risks:
        name = r.definition['title']
        if not risk_complete(r):
            issues.append(f'{name}: complete ratings, controls and rationale (or justify non-applicability).')
        if r.not_applicable:
            continue
        if not any(evidence_current(e) for e in r.evidence):
            issues.append(f'{name}: link current evidence verified by a reviewer.')
        level = rating(r.residual_likelihood, r.residual_impact)
        if level == 'Critical':
            issues.append(f'{name}: critical residual risk requires redesign or rejection.')
        elif level == 'High' and not conditional:
            issues.append(f'{name}: high residual risk requires a conditional decision or further mitigation.')
        resolved = action_resolved(r)
        if (r.action and not resolved) or (level == 'High' and not resolved):
            if not conditional:
                issues.append(f'{name}: an action remains open.')
            if not r.action or not r.owner_id or not r.due_date or r.due_date < date.today():
                issues.append(f'{name}: conditional approval needs an open action, owner and future due date.')
        if level == 'High' and r.action_done and not r.action_updates:
            issues.append(f'{name}: high residual risk requires a further action or a reduced rating supported by evidence.')
    return issues


def assessment_payload(a):
    events = db.session.scalars(select(AuditEvent).filter_by(entity='assessment', entity_id=a.id).order_by(AuditEvent.id)).all()
    return {'id': a.id, 'catalog_version': a.catalog_version, 'created_at': a.created_at.isoformat(),
            'history': [{'action': e.action, 'actor': e.actor.name if e.actor else None,
                         'created_at': e.created_at.isoformat(), 'details': e.details} for e in events],
            'system_context': a.snapshot, 'state': a.state, 'context_changed': stale(a),
            'current_attention': attention(a),
            'legal_review': a.legal_review, 'decision': a.decision,
            'reviewer': a.reviewer.name if a.reviewer else None,
            'decided_at': a.decided_at.isoformat() if a.decided_at else None,
            'review_due': a.review_due.isoformat() if a.review_due else None,
            'approval_blockers': blockers(a, a.state == 'conditional'),
            'scoring': 'Local prioritization policy: likelihood × impact, Low 1–5, Moderate 6–11, High 12–19, Critical 20–25. Not an EU legal classification.',
            'risks': [{'id': r.id, 'definition': r.definition, 'not_applicable': r.not_applicable,
                      'likelihood': r.likelihood, 'impact': r.impact, 'residual_likelihood': r.residual_likelihood,
                      'residual_impact': r.residual_impact, 'inherent': rating(r.likelihood, r.impact),
                      'residual': rating(r.residual_likelihood, r.residual_impact), 'rationale': r.rationale,
                      'controls': r.controls, 'action': r.action, 'owner': r.owner.name if r.owner else None,
                      'due_date': str(r.due_date) if r.due_date else None, 'action_done': r.action_done,
                      'action_resolved_now': action_resolved(r),
                      'follow_up': [{'notes': u.notes, 'url': u.url, 'author': u.author.name,
                                    'created_at': u.created_at.isoformat(),
                                    'verified_by': u.verifier.name if u.verifier else None,
                                    'verified_at': u.verified_at.isoformat() if u.verified_at else None}
                                   for u in r.action_updates],
                      'evidence': [{'title': e.title, 'url': e.url, 'notes': e.notes,
                                    'expires_on': str(e.expires_on), 'verified_by': e.verifier.name if e.verifier else None,
                                    'verified_at': str(e.verified_at) if e.verified_at else None,
                                    'current': evidence_current(e)} for e in r.evidence]} for r in a.risks]}
