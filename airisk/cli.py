import click
from datetime import date, timedelta
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from .models import db, User, Vendor, AISystem, Evidence
from .services import new_assessment


def register_commands(app):
    @app.cli.command('create-user')
    @click.option('--email', prompt=True)
    @click.option('--name', prompt=True)
    @click.option('--role', type=click.Choice(['admin', 'reviewer', 'member']), default='member')
    @click.password_option()
    def create_user(email, name, role, password):
        """Create an account in this organization's private workspace."""
        email, name = email.strip().lower(), name.strip()
        if '@' not in email or len(email) > 254 or not name or len(name) > 100 or len(password) < 12:
            raise click.ClickException('Use a valid email, a name and a password of at least 12 characters.')
        if db.session.scalar(select(User).filter_by(email=email)):
            raise click.ClickException('That email already exists.')
        db.session.add(User(email=email, name=name, role=role, password_hash=generate_password_hash(password)))
        db.session.commit()
        click.echo('Account created.')

    @app.cli.command('reset-password')
    @click.option('--email', prompt=True)
    @click.password_option()
    def reset_password(email, password):
        """Reset an account password with server administrator access."""
        u = db.session.scalar(select(User).filter_by(email=email.strip().lower()))
        if not u or len(password) < 12:
            raise click.ClickException('An existing account and a password of at least 12 characters are required.')
        u.password_hash = generate_password_hash(password)
        u.session_version += 1
        db.session.commit()
        click.echo('Password updated. Existing sessions for this account have been revoked.')

    @app.cli.command('seed-demo')
    @click.option('--owner', required=True, help='Email of an existing account.')
    def seed_demo(owner):
        """Add three clearly labeled synthetic use cases, without creating credentials."""
        u = db.session.scalar(select(User).filter_by(email=owner.lower()))
        if not u:
            raise click.ClickException('Create the owner account first.')
        if db.session.scalar(select(AISystem).where(AISystem.name.like('[Demo]%'))):
            raise click.ClickException('Demo records already exist.')
        seed_examples(u)
        db.session.commit()
        click.echo('Three synthetic examples created. They are drafts, not approved assessments.')


def seed_examples(owner):
    v = Vendor(name='[Demo] Northstar AI', product='Enterprise assistant', training_use='no',
               retention='Synthetic contract: prompts retained 30 days; deletion must be tested.',
               regions='EU hosting declared; subprocessors require review.',
               notes='Fictional vendor for workflow exploration. No actual supplier has been assessed.',
               review_due=date.today() + timedelta(days=90))
    db.session.add(v)
    db.session.flush()
    cases = [
        ('Internal knowledge assistant', 'Help employees find internal policies using permission-aware retrieval.', 'generative', 'internal', 'confidential', 'advisory', True, 'general'),
        ('Customer service agent', 'Draft customer responses and propose refunds for a human to authorize.', 'agentic', 'external', 'personal', 'assisted', True, 'general'),
        ('Recruitment screening', 'Rank applications to support interview selection. Assess impacts on applicants.', 'predictive', 'internal', 'personal', 'advisory', False, 'employment'),
    ]
    for name, purpose, kind, audience, sensitivity, autonomy, rag, domain in cases:
        system = AISystem(name='[Demo] ' + name, purpose=purpose, department='Example team', owner_id=owner.id,
                          vendor_id=v.id, model_version='Demo model v1 (synthetic)', ai_type=kind, lifecycle='proposed',
                          data_sensitivity=sensitivity, audience=audience, autonomy=autonomy, uses_rag=rag,
                          eu_scope='yes', operator_role='deployer', use_domain=domain, prohibited='none',
                          context_notes='Synthetic example. Verify every assumption before assessing a real system.')
        db.session.add(system)
        db.session.flush()
        a = new_assessment(system, owner.id)
        r = a.risks[0]
        r.likelihood, r.impact, r.residual_likelihood, r.residual_impact = 4, 4, 2, 4
        r.rationale = 'Example only: plausible errors could influence work. A representative evaluation is still needed.'
        r.controls = 'Planned: source citations and human review. Effectiveness is not yet demonstrated.'
        r.action, r.owner_id, r.due_date = 'Run a representative evaluation and attach the results.', owner.id, date.today() + timedelta(days=14)
