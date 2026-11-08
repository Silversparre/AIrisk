from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData

db = SQLAlchemy(metadata=MetaData(naming_convention={
    'ix': 'ix_%(column_0_label)s', 'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s', 'pk': 'pk_%(table_name)s',
}))


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(512), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='member')
    active = db.Column(db.Boolean, nullable=False, default=True)
    session_version = db.Column(db.Integer, nullable=False, default=1)


class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    product = db.Column(db.String(120), nullable=False)
    website = db.Column(db.String(500), default='')
    training_use = db.Column(db.String(20), nullable=False, default='unknown')
    retention = db.Column(db.String(500), default='')
    regions = db.Column(db.String(250), default='')
    notes = db.Column(db.Text, default='')
    review_due = db.Column(db.Date)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    evidence = db.relationship('Evidence', backref='vendor', lazy=True)


class AISystem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    purpose = db.Column(db.Text, nullable=False)
    department = db.Column(db.String(100), default='')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    owner = db.relationship('User')
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'))
    vendor = db.relationship('Vendor', backref='systems')
    model_version = db.Column(db.String(160), nullable=False)
    ai_type = db.Column(db.String(20), nullable=False)
    lifecycle = db.Column(db.String(20), nullable=False, default='proposed')
    data_sensitivity = db.Column(db.String(20), nullable=False)
    audience = db.Column(db.String(20), nullable=False)
    autonomy = db.Column(db.String(20), nullable=False)
    uses_rag = db.Column(db.Boolean, nullable=False, default=False)
    eu_scope = db.Column(db.String(20), nullable=False)
    operator_role = db.Column(db.String(20), nullable=False)
    use_domain = db.Column(db.String(30), nullable=False)
    prohibited = db.Column(db.String(20), nullable=False, default='unknown')
    context_notes = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    assessments = db.relationship('Assessment', backref='system', order_by='Assessment.id.desc()', lazy=True)


class Assessment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=1, server_default='1')
    system_id = db.Column(db.Integer, db.ForeignKey('ai_system.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    author = db.relationship('User', foreign_keys=[author_id])
    catalog_version = db.Column(db.String(40), nullable=False)
    snapshot = db.Column(db.JSON, nullable=False)
    state = db.Column(db.String(20), nullable=False, default='draft')
    legal_review = db.Column(db.Text, default='')
    legal_reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    decision = db.Column(db.Text, default='')
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    decided_at = db.Column(db.DateTime)
    review_due = db.Column(db.Date)
    risks = db.relationship('Risk', backref='assessment', order_by='Risk.id', cascade='all, delete-orphan')
    evidence = db.relationship('Evidence', backref='assessment', lazy=True)
    __mapper_args__ = {'version_id_col': revision}


risk_evidence = db.Table('risk_evidence',
    db.Column('risk_id', db.Integer, db.ForeignKey('risk.id'), primary_key=True),
    db.Column('evidence_id', db.Integer, db.ForeignKey('evidence.id'), primary_key=True))


class Risk(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=1)
    assessment_id = db.Column(db.Integer, db.ForeignKey('assessment.id'), nullable=False)
    definition = db.Column(db.JSON, nullable=False)
    likelihood = db.Column(db.Integer)
    impact = db.Column(db.Integer)
    residual_likelihood = db.Column(db.Integer)
    residual_impact = db.Column(db.Integer)
    rationale = db.Column(db.Text, default='')
    controls = db.Column(db.Text, default='')
    not_applicable = db.Column(db.Boolean, nullable=False, default=False)
    action = db.Column(db.Text, default='')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    owner = db.relationship('User')
    due_date = db.Column(db.Date)
    action_done = db.Column(db.Boolean, nullable=False, default=False)
    evidence = db.relationship('Evidence', secondary=risk_evidence, backref='risks')
    action_updates = db.relationship('ActionUpdate', backref='risk', order_by='ActionUpdate.id.desc()')
    __mapper_args__ = {'version_id_col': revision}
    __table_args__ = tuple(db.CheckConstraint(f'{field} BETWEEN 1 AND 5', name=field+'_range')
                          for field in ('likelihood', 'impact', 'residual_likelihood', 'residual_impact'))


class Evidence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey('assessment.id'))
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'))
    title = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(1000), default='')
    notes = db.Column(db.Text, nullable=False)
    added_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now, nullable=False)
    expires_on = db.Column(db.Date, nullable=False)
    verified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    verifier = db.relationship('User', foreign_keys=[verified_by])
    verified_at = db.Column(db.DateTime)


class ActionUpdate(db.Model):
    """Follow-up evidence; never overwrites the original decision or risk ratings."""
    id = db.Column(db.Integer, primary_key=True)
    risk_id = db.Column(db.Integer, db.ForeignKey('risk.id'), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    url = db.Column(db.String(1000), default='')
    added_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    author = db.relationship('User', foreign_keys=[added_by])
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    verified_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    verifier = db.relationship('User', foreign_keys=[verified_by])
    verified_at = db.Column(db.DateTime)


class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    actor = db.relationship('User')
    entity = db.Column(db.String(30), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=now)


class LoginAttempt(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    window_start = db.Column(db.DateTime, nullable=False, default=now)


class AuthSession(db.Model):
    token_hash = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


class SetupClaim(db.Model):
    """A unique row makes first-account creation a single atomic operation."""
    id = db.Column(db.Integer, primary_key=True)


class ClaudeExchange(db.Model):
    """Human-initiated web handoff; imported content stays advisory until accepted."""
    id = db.Column(db.Integer, primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=1)
    assessment_id = db.Column(db.Integer, db.ForeignKey('assessment.id'), nullable=False)
    assessment = db.relationship('Assessment', backref='claude_exchanges')
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    author = db.relationship('User')
    mode = db.Column(db.String(20), nullable=False)
    workspace = db.Column(db.String(30), nullable=False)
    model_label = db.Column(db.String(160), default='')
    context_digest = db.Column(db.String(64), nullable=False)
    request_context = db.Column(db.JSON, nullable=False)
    prompt = db.Column(db.Text, nullable=False, default='')
    response = db.Column(db.JSON)
    accepted_risk_ids = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    imported_at = db.Column(db.DateTime)
    accepted_at = db.Column(db.DateTime)
    __mapper_args__ = {'version_id_col': revision}
