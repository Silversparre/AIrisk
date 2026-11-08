import csv
import io
from uuid import uuid4
from datetime import date
from functools import wraps
from urllib.parse import urlsplit
from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for, Response
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from .models import db, User, Vendor, AISystem, Assessment, Risk, Evidence, AuditEvent, ActionUpdate, AuthSession, SetupClaim, now
from .security import (accept_setup_link, consume_login_budget, current_session, record_id,
                       reset_account_budget, revoke_current_session, setup_access, start_session)
from .catalog import OPTIONS, SOURCES, VERSION, REVIEWED_ON, REVIEW_BY, triage
from .services import (new_assessment, audit, rating, stale, risk_complete, evidence_current,
                       blockers, assessment_payload, attention, action_resolved, TERMINAL)

bp = Blueprint('web', __name__)
DUMMY_HASH = generate_password_hash('invalid-account-password')


def text(name, limit=4000, required=False):
    raw = request.form.get(name, '')
    value = raw if name in ('password', 'current_password') else raw.strip()
    if len(value) > limit or (required and not value):
        abort(400, description=f'{name.replace("_", " ").title()} is required and must fit within {limit} characters.' if required else f'{name} exceeds {limit} characters.')
    return value


def choice(name):
    value = text(name, 50, True)
    if value not in dict(OPTIONS[name]):
        abort(400, description=f'Choose a valid {name.replace("_", " ")}.')
    return value


def number(name, required=False):
    value = text(name, 12, required)
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        abort(400, description=f'{name} must be an integer.')
    if parsed < 1:
        abort(400, description=f'{name} must be positive.')
    return parsed


def day(name, required=False):
    value = text(name, 10, required)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        abort(400, description=f'{name} must be a valid date.')


def safe_url(name):
    value = text(name, 1000)
    try:
        url = urlsplit(value)
        if value and (url.scheme != 'https' or not url.hostname or url.username or url.password):
            raise ValueError()
    except ValueError:
        abort(400, description='Use an HTTPS URL without embedded credentials.')
    return value


def user_id(name='owner_id', required=False):
    value = number(name, required)
    if value and not db.session.scalar(select(User).filter_by(id=value, active=True)):
        abort(400, description='Choose an active workspace member.')
    return value


def signed_in(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for('web.login'))
        return fn(*args, **kwargs)
    return wrapped


def reviewer():
    if g.user.role not in ('admin', 'reviewer'):
        abort(403, description='A reviewer or administrator must perform this action.')


def independent(a):
    if g.user.id in (a.author_id, a.system.owner_id):
        return False
    return not db.session.scalar(select(AuditEvent.id).where(
        AuditEvent.entity == 'assessment', AuditEvent.entity_id == a.id,
        AuditEvent.actor_id == g.user.id,
        AuditEvent.action.in_(('created', 'risk updated', 'custom risk added'))).limit(1))


def edit_assessment(a):
    if a.state != 'draft':
        abort(409, description='This assessment is locked. Return it to draft before making changes.')
    if g.user.id not in (a.author_id, a.system.owner_id) and g.user.role != 'admin':
        abort(403)
    if stale(a):
        abort(409, description='The system context changed. Start a new assessment to use current information.')


@bp.before_app_request
def load_user():
    uid = session.get('user_id')
    auth = current_session() if type(uid) is int else None
    g.user = db.session.get(User, uid) if auth else None
    if uid and not auth:
        session.clear()
    if g.user and (not g.user.active or session.get('session_version') != g.user.session_version):
        session.clear()
        g.user = None


@bp.app_context_processor
def template_context():
    return dict(rating=rating, stale=stale, complete=risk_complete, evidence_current=evidence_current,
                attention=attention, action_resolved=action_resolved,
                today=date.today(), options=OPTIONS, catalog_version=VERSION,
                label=lambda field, value: dict(OPTIONS[field]).get(value, value))


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if not current_app.config['PRODUCTION'] and not db.session.scalar(select(User.id).limit(1)):
        return redirect(url_for('web.setup'))
    if request.method == 'POST':
        email = text('email', 254, True).lower()
        password = text('password', 1024, True)
        consume_login_budget(email)
        u = db.session.scalar(select(User).filter_by(email=email, active=True))
        valid = check_password_hash(u.password_hash if u else DUMMY_HASH, password)
        if not u or not valid:
            flash('Email or password was not recognized.', 'error')
            return render_template('login.html'), 401
        reset_account_budget(email)
        start_session(u)
        audit(u.id, 'user', u.id, 'signed in')
        db.session.commit()
        return redirect(url_for('web.dashboard'))
    return render_template('login.html')


@bp.post('/logout')
@signed_in
def logout():
    revoke_current_session()
    audit(g.user.id, 'user', g.user.id, 'signed out')
    db.session.commit()
    session.clear()
    return redirect(url_for('web.login'))


@bp.get('/health')
def health():
    try:
        db.session.execute(select(User.id).limit(1))
    except Exception:
        db.session.rollback()
        return jsonify(status='unavailable'), 503
    return jsonify(status='ok')


@bp.get('/')
@signed_in
def dashboard():
    systems = db.session.scalars(select(AISystem).order_by(AISystem.created_at.desc())).all()
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.created_at.desc())).all()
    actions = [r for r in db.session.scalars(select(Risk).where(Risk.action != '', Risk.action_done.is_(False)).order_by(Risk.due_date))
               if not action_resolved(r) and r.assessment.state != 'rejected']
    return render_template('dashboard.html', systems=systems, assessments=assessments, actions=actions)


@bp.get('/reviews')
@signed_in
def reviews():
    assessments = db.session.scalars(select(Assessment).order_by(Assessment.created_at.desc())).all()
    return render_template('reviews.html', assessments=assessments)


@bp.route('/systems/new', methods=['GET', 'POST'])
@bp.route('/systems/<int:system_id>/edit', methods=['GET', 'POST'])
@signed_in
def system_form(system_id=None):
    system = db.get_or_404(AISystem, system_id) if system_id else None
    from .models import ExternalLink
    from .enterprise_workflow import can_manage as manage_intake
    intake_id = number('coupa_intake') if request.method == 'POST' else request.args.get('coupa_intake', type=int)
    intake = db.get_or_404(ExternalLink, intake_id) if intake_id else None
    if intake:
        if system_id or intake.provider != 'coupa' or not manage_intake(intake, g.user):
            abort(403)
        if intake.system_id:
            return redirect(url_for('web.system_detail', system_id=intake.system_id))
    if system and g.user.id != system.owner_id and g.user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        fields = {key: choice(key) for key in OPTIONS if key != 'training_use'}
        fields.update(name=text('name', 120, True), purpose=text('purpose', 4000, True),
                      department=text('department', 100), model_version=text('model_version', 160, True),
                      owner_id=user_id(required=True), vendor_id=number('vendor_id'),
                      uses_rag=request.form.get('uses_rag') == 'yes', context_notes=text('context_notes', 4000))
        if fields['vendor_id'] and not db.session.get(Vendor, fields['vendor_id']):
            abort(400, description='Choose an existing vendor.')
        if intake and fields['vendor_id'] != intake.vendor_id:
            abort(400, description='Keep the imported supplier/product for this Coupa line.')
        if system:
            for key, value in fields.items():
                setattr(system, key, value)
        else:
            system = AISystem(**fields)
            db.session.add(system)
        db.session.flush()
        audit(g.user.id, 'system', system.id, 'updated' if system_id else 'created')
        if intake:
            intake.system_id = system.id
            audit(g.user.id, 'system', system.id, 'coupa intake linked', {'link_id': intake.id})
        db.session.commit()
        return redirect(url_for('web.system_detail', system_id=system.id))
    return render_template('system_form.html', system=system, coupa_intake=intake,
        users=db.session.scalars(select(User).where(User.active.is_(True))).all(),
        vendors=db.session.scalars(select(Vendor).order_by(Vendor.name)).all())


@bp.get('/systems/<int:system_id>')
@signed_in
def system_detail(system_id):
    system = db.get_or_404(AISystem, system_id)
    from .services import context
    return render_template('system_detail.html', system=system, triage=triage(context(system)))


@bp.post('/systems/<int:system_id>/assess')
@signed_in
def assess(system_id):
    system = db.get_or_404(AISystem, system_id)
    if g.user.id != system.owner_id and g.user.role != 'admin':
        abort(403)
    a = new_assessment(system, g.user.id)
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=a.id))


@bp.get('/assessments/<int:assessment_id>')
@signed_in
def assessment(assessment_id):
    a = db.get_or_404(Assessment, assessment_id)
    candidates = list(a.evidence) + (list(a.system.vendor.evidence) if a.system.vendor else [])
    events = db.session.scalars(select(AuditEvent).filter_by(entity='assessment', entity_id=a.id).order_by(AuditEvent.id.desc())).all()
    return render_template('assessment.html', a=a, evidence=candidates,
        users=db.session.scalars(select(User).where(User.active.is_(True))).all(),
        blockers=blockers(a, True), events=events, independent=independent(a),
        can_edit=a.state == 'draft' and not stale(a) and (g.user.id in (a.author_id, a.system.owner_id) or g.user.role == 'admin'))


@bp.post('/risks/<int:risk_id>')
@signed_in
def save_risk(risk_id):
    r = db.get_or_404(Risk, risk_id)
    a = r.assessment
    edit_assessment(a)
    if number('revision', True) != r.revision:
        abort(409, description='This risk changed in another tab. Reload before saving so you do not overwrite newer work.')
    for field in ('likelihood', 'impact', 'residual_likelihood', 'residual_impact'):
        value = number(field)
        if value is not None and value > 5:
            abort(400, description='Risk ratings must be 1–5. Leave unknown values blank.')
        setattr(r, field, value)
    r.rationale, r.controls = text('rationale'), text('controls')
    r.not_applicable = request.form.get('not_applicable') == 'yes'
    if r.not_applicable and not r.rationale:
        abort(400, description='Explain why this risk does not apply.')
    r.action, r.owner_id, r.due_date = text('action', 2000), user_id(), day('due_date')
    r.action_done = request.form.get('action_done') == 'yes'
    if r.action and (not r.owner_id or not r.due_date):
        abort(400, description='Every action needs an owner and due date.')
    selected = []
    for value in request.form.getlist('evidence_ids'):
        e = db.get_or_404(Evidence, record_id(value))
        if e.assessment_id != a.id and not (a.system.vendor_id and e.vendor_id == a.system.vendor_id):
            abort(403, description='Evidence must belong to this assessment or its vendor.')
        selected.append(e)
    r.evidence = list({e.id: e for e in selected}.values())
    r.revision += 1  # Also version changes to evidence links, which live in a separate table.
    a.revision += 1  # Serialize edits against review/decision transitions as well as this risk.
    audit(g.user.id, 'assessment', a.id, 'risk updated', {'risk': r.definition['key']})
    db.session.commit()
    flash('Risk saved.', 'success')
    return redirect(url_for('web.assessment', assessment_id=a.id, _anchor=f'risk-{r.id}'))


@bp.post('/assessments/<int:assessment_id>/risks')
@signed_in
def add_custom_risk(assessment_id):
    a = db.get_or_404(Assessment, assessment_id)
    edit_assessment(a)
    definition = dict(key='custom-' + uuid4().hex[:8], title=text('title', 200, True),
        scenario=text('scenario', 4000, True), question='What could happen, to whom, and how is it controlled?',
        control=text('control', 2000, True), expected_evidence=text('expected_evidence', 2000, True),
        sources=[], origin='Organization-defined scenario; no automatic framework mapping')
    a.risks.append(Risk(definition=definition))
    a.revision += 1
    audit(g.user.id, 'assessment', a.id, 'custom risk added', {'title': definition['title']})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=a.id, _anchor='risks'))


@bp.post('/risks/<int:risk_id>/follow-up')
@signed_in
def action_update(risk_id):
    r = db.get_or_404(Risk, risk_id)
    if r.assessment.state != 'conditional' or not r.action or action_resolved(r):
        abort(409, description='Follow-up is available for open actions on conditional decisions.')
    if g.user.id not in (r.owner_id, r.assessment.system.owner_id) and g.user.role != 'admin':
        abort(403)
    u = ActionUpdate(risk=r, notes=text('notes', 4000, True), url=safe_url('url'), added_by=g.user.id)
    db.session.add(u)
    r.revision += 1
    audit(g.user.id, 'assessment', r.assessment_id, 'action completion submitted', {'risk_id': r.id})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=r.assessment_id, _anchor=f'risk-{r.id}'))


@bp.post('/action-updates/<int:update_id>/verify')
@signed_in
def verify_action(update_id):
    reviewer()
    u = db.get_or_404(ActionUpdate, update_id)
    if u.added_by == g.user.id:
        abort(403, description='Another reviewer must verify your action completion evidence.')
    if u.verified_at or action_resolved(u.risk):
        abort(409, description='This action is already resolved.')
    u.verified_by, u.verified_at = g.user.id, now()
    u.risk.revision += 1
    audit(g.user.id, 'assessment', u.risk.assessment_id, 'action completion verified', {'risk_id': u.risk_id, 'update_id': u.id})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=u.risk.assessment_id, _anchor=f'risk-{u.risk_id}'))


@bp.post('/assessments/<int:assessment_id>/submit')
@signed_in
def submit(assessment_id):
    a = db.get_or_404(Assessment, assessment_id)
    edit_assessment(a)
    if not all(risk_complete(r) for r in a.risks):
        abort(400, description='Complete each risk or explain why it does not apply before requesting review.')
    a.state = 'in_review'
    audit(g.user.id, 'assessment', a.id, 'submitted for review')
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=a.id))


@bp.post('/assessments/<int:assessment_id>/reopen')
@signed_in
def reopen(assessment_id):
    a = db.get_or_404(Assessment, assessment_id)
    reviewer()
    if not independent(a):
        abort(403, description='An independent reviewer must return the assessment for changes.')
    if a.state != 'in_review':
        abort(409, description='Decided assessments are immutable. Create a new assessment for follow-up.')
    if stale(a):
        abort(409, description='Create a new assessment for the changed system context.')
    reason = text('reason', 2000, True)
    a.state, a.decision, a.reviewer_id, a.decided_at, a.review_due = 'draft', '', None, None, None
    a.legal_review, a.legal_reviewer_id = '', None
    audit(g.user.id, 'assessment', a.id, 'returned to draft; applicability review cleared', {'reason': reason})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=a.id))


@bp.post('/assessments/<int:assessment_id>/legal')
@signed_in
def legal(assessment_id):
    a = db.get_or_404(Assessment, assessment_id)
    reviewer()
    if a.state != 'in_review' or not independent(a) or stale(a):
        abort(409, description='An independent reviewer can record applicability on a current assessment in review.')
    a.legal_review, a.legal_reviewer_id = text('legal_review', 6000, True), g.user.id
    audit(g.user.id, 'assessment', a.id, 'legal applicability reviewed', {'rationale': a.legal_review})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=a.id, _anchor='decision'))


@bp.post('/assessments/<int:assessment_id>/decide')
@signed_in
def decide(assessment_id):
    a = db.get_or_404(Assessment, assessment_id)
    reviewer()
    if not independent(a):
        abort(403, description='The system owner, assessment author or a contributor to its risk answers cannot make the review decision.')
    if a.state != 'in_review':
        abort(409, description='Only an assessment in review can receive a decision.')
    state = text('state', 20, True)
    if state not in TERMINAL:
        abort(400)
    decision = text('decision', 6000, True)
    due = day('review_due', state != 'rejected')
    if due and due <= date.today():
        abort(400, description='Choose a future review date.')
    if state != 'rejected':
        issues = blockers(a, state == 'conditional')
        if issues:
            abort(400, description='Decision blocked: ' + ' '.join(issues))
    a.state, a.decision, a.review_due = state, decision, due
    a.reviewer_id, a.decided_at = g.user.id, now()
    audit(g.user.id, 'assessment', a.id, 'decision: ' + state, {'rationale': decision, 'review_due': str(due)})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=a.id))


@bp.post('/evidence')
@signed_in
def add_evidence():
    aid, vid = number('assessment_id'), number('vendor_id')
    if bool(aid) == bool(vid):
        abort(400, description='Evidence must have exactly one assessment or vendor scope.')
    if aid:
        a = db.get_or_404(Assessment, aid)
        edit_assessment(a)
    else:
        db.get_or_404(Vendor, vid)
    expires = day('expires_on', True)
    if expires <= date.today():
        abort(400, description='Choose a future evidence review date.')
    e = Evidence(assessment_id=aid, vendor_id=vid, title=text('title', 200, True),
                 url=safe_url('url'), notes=text('notes', 8000, True), expires_on=expires, added_by=g.user.id)
    db.session.add(e)
    db.session.flush()
    audit(g.user.id, 'assessment' if aid else 'vendor', aid or vid, 'evidence added', {'evidence_id': e.id})
    db.session.commit()
    flash('Evidence recorded. Link it to the risks it supports.', 'success')
    return redirect(url_for('web.assessment', assessment_id=aid, _anchor='evidence') if aid else url_for('web.vendor_detail', vendor_id=vid))


@bp.post('/evidence/<int:evidence_id>/verify')
@signed_in
def verify_evidence(evidence_id):
    reviewer()
    e = db.get_or_404(Evidence, evidence_id)
    if e.added_by == g.user.id:
        abort(403, description='Another reviewer must verify evidence you added.')
    if e.expires_on < date.today():
        abort(400, description='Evidence expired. Add updated evidence.')
    if e.verified_at:
        abort(409, description='Evidence verification is already recorded. Add a new evidence record for later reviews.')
    if e.assessment and e.assessment.state in TERMINAL:
        abort(409, description='The assessment is locked.')
    e.verified_at, e.verified_by = now(), g.user.id
    audit(g.user.id, 'assessment' if e.assessment_id else 'vendor', e.assessment_id or e.vendor_id,
          'evidence verified', {'evidence_id': e.id})
    db.session.commit()
    return redirect(url_for('web.assessment', assessment_id=e.assessment_id, _anchor='evidence') if e.assessment_id else url_for('web.vendor_detail', vendor_id=e.vendor_id))


@bp.route('/vendors', methods=['GET', 'POST'])
@signed_in
def vendors():
    if request.method == 'POST':
        v = Vendor(name=text('name', 120, True), product=text('product', 120, True), website=safe_url('website'),
                   training_use=choice('training_use'), retention=text('retention', 500), regions=text('regions', 250),
                   notes=text('notes', 6000), review_due=day('review_due', True))
        db.session.add(v)
        db.session.flush()
        audit(g.user.id, 'vendor', v.id, 'created')
        db.session.commit()
        return redirect(url_for('web.vendor_detail', vendor_id=v.id))
    return render_template('vendors.html', vendors=db.session.scalars(select(Vendor).order_by(Vendor.name)).all())


@bp.route('/vendors/<int:vendor_id>', methods=['GET', 'POST'])
@signed_in
def vendor_detail(vendor_id):
    v = db.get_or_404(Vendor, vendor_id)
    if request.method == 'POST':
        reviewer()
        v.training_use, v.retention = choice('training_use'), text('retention', 500)
        v.regions, v.notes, v.review_due = text('regions', 250), text('notes', 6000), day('review_due', True)
        audit(g.user.id, 'vendor', v.id, 'vendor facts updated')
        db.session.commit()
        flash('Vendor updated. Assessments using changed facts require reassessment.', 'success')
        return redirect(url_for('web.vendor_detail', vendor_id=v.id))
    return render_template('vendor_detail.html', vendor=v)


@bp.get('/sources')
@signed_in
def sources():
    return render_template('sources.html', sources=SOURCES, reviewed_on=REVIEWED_ON, review_by=REVIEW_BY)


@bp.get('/assessments/<int:assessment_id>/export.<format>')
@signed_in
def export(assessment_id, format):
    a = db.get_or_404(Assessment, assessment_id)
    payload = assessment_payload(a)
    if format == 'json':
        response = jsonify(payload)
        response.headers['Content-Disposition'] = f'attachment; filename=airisk-assessment-{a.id}.json'
        return response
    if format == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['System', 'Risk', 'Inherent', 'Residual', 'Rationale', 'Controls', 'Action', 'Owner', 'Due', 'State', 'Catalog'])
        def cell(value):
            value = str(value or '')
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value
        # Spreadsheet formula protection also applies to names and explanatory text.
        for r in payload['risks']:
            writer.writerow([cell(x) for x in [a.snapshot['name'], r['definition']['title'], r['inherent'], r['residual'],
                             r['rationale'], r['controls'], r['action'], r['owner'], r['due_date'], a.state, a.catalog_version]])
        return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=airisk-assessment-{a.id}.csv'})
    if format == 'html':
        return render_template('report.html', a=a, payload=payload)
    abort(404)


@bp.route('/setup', methods=['GET', 'POST'])
def setup():
    if current_app.config['PRODUCTION'] or request.remote_addr not in ('127.0.0.1', '::1'):
        abort(404)
    if db.session.scalar(select(User.id).limit(1)):
        return redirect(url_for('web.login'))
    if request.method == 'GET' and accept_setup_link():
        return redirect(url_for('web.setup'))
    if not setup_access():
        abort(403, description='Open the private setup link shown by the local launcher, or create the first account with the administrator CLI.')
    if request.method == 'POST':
        email, name, password = text('email', 254, True).lower(), text('name', 100, True), text('password', 1024, True)
        if '@' not in email or len(password) < 12:
            abort(400, description='Use a valid email and a password of at least 12 characters.')
        u = User(email=email, name=name, password_hash=generate_password_hash(password), role='admin')
        db.session.add(SetupClaim(id=1))
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            abort(409, description='Workspace setup was already completed. Sign in with the created account.')
        db.session.add(u)
        db.session.flush()
        audit(u.id, 'user', u.id, 'workspace initialized')
        start_session(u)
        db.session.commit()
        return redirect(url_for('web.team'))
    return render_template('setup.html')


@bp.route('/team', methods=['GET', 'POST'])
@signed_in
def team():
    if g.user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        email, name, password = text('email', 254, True).lower(), text('name', 100, True), text('password', 1024, True)
        role = text('role', 20, True)
        if role not in ('member', 'reviewer', 'admin') or '@' not in email or len(password) < 12:
            abort(400, description='Use a valid role, email and a password of at least 12 characters.')
        if db.session.scalar(select(User).filter_by(email=email)):
            abort(400, description='This account already exists.')
        u = User(email=email, name=name, password_hash=generate_password_hash(password), role=role)
        db.session.add(u)
        db.session.flush()
        audit(g.user.id, 'user', u.id, 'account created', {'role': role})
        db.session.commit()
        flash('Account created. Share the credentials through your approved internal channel.', 'success')
        return redirect(url_for('web.team'))
    return render_template('team.html', users=db.session.scalars(select(User).order_by(User.name)).all())


@bp.post('/team/<int:user_id>/toggle')
@signed_in
def toggle_user(user_id):
    if g.user.role != 'admin' or g.user.id == user_id:
        abort(403)
    u = db.get_or_404(User, user_id)
    u.active = not u.active
    u.session_version += 1
    db.session.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
    audit(g.user.id, 'user', u.id, 'account enabled' if u.active else 'account disabled')
    db.session.commit()
    return redirect(url_for('web.team'))


@bp.route('/account', methods=['GET', 'POST'])
@signed_in
def account():
    if request.method == 'POST':
        old, new = text('current_password', 1024, True), text('password', 1024, True)
        if not check_password_hash(g.user.password_hash, old) or len(new) < 12:
            abort(400, description='Supply the current password and a new password of at least 12 characters.')
        g.user.password_hash = generate_password_hash(new)
        g.user.session_version += 1
        db.session.execute(delete(AuthSession).where(AuthSession.user_id == g.user.id))
        start_session(g.user)
        audit(g.user.id, 'user', g.user.id, 'password changed')
        db.session.commit()
        flash('Password changed. Your other sessions have been revoked.', 'success')
        return redirect(url_for('web.account'))
    return render_template('account.html')
