"""Durable ticket creation and read-only progress polling, shared by UI and CLI."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from .halo import HaloClient, HaloConfig, HaloError, clean_text, identifier
from .models import db, HaloTicket, now
from .services import action_context, action_resolved, audit, stale


class HaloBusy(HaloError):
    pass


def _commit():
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        raise HaloBusy('A newer operation changed this ticket. Reload to see its result.') from None


def can_manage(risk, user):
    return user.role == 'admin' or user.id in (
        risk.owner_id, risk.assessment.author_id, risk.assessment.system.owner_id)


def check_action(risk):
    if (risk.assessment.state not in ('draft', 'in_review', 'conditional') or risk.not_applicable
            or not risk.action or not risk.owner or not risk.owner.active or not risk.due_date
            or action_resolved(risk)):
        raise HaloError('Choose an open, applicable action with an active owner and due date.')
    if risk.assessment.state != 'conditional' and stale(risk.assessment):
        raise HaloError('The assessment context changed. Reassess before creating a remediation ticket.')


def make_payload(risk, config, correlation):
    reference = f'[AIrisk:{correlation}]'
    assessment = risk.assessment
    details = (f'{reference}\nAIrisk assessment #{assessment.id}, action #{risk.id}\n'
               f'AI system: {assessment.snapshot["name"]}\nRisk: {risk.definition["title"]}\n\n'
               f'Remediation action:\n{risk.action}\n\n'
               f'Accountable owner: {risk.owner.name} ({risk.owner.email})\n'
               f'Due date: {risk.due_date.isoformat()}\n\n'
               'Record implementation results and evidence in this ticket. '
               'Completion requires independent verification in AIrisk.')
    if config.public_url:
        details += f'\nAssessment: {config.public_url}/assessments/{assessment.id}#risk-{risk.id}'
    return {**config.defaults, 'summary': f'{reference} {risk.definition["title"]}'[:255],
            'details': details, 'targetdate': risk.due_date.isoformat() + 'T23:59:59Z'}


def _same_instance(link, config):
    if link.api_url != config.api_url:
        raise HaloError('This ticket belongs to a different Halo instance. Restore its original API configuration.')


def _claim(link, *, creating=False):
    moment, token = now(), str(uuid4())
    conditions = [HaloTicket.id == link.id, HaloTicket.state == link.state,
                  HaloTicket.ticket_id == link.ticket_id,
                  HaloTicket.revision == link.revision,
                  or_(HaloTicket.lease_until.is_(None), HaloTicket.lease_until <= moment)]
    if creating:
        conditions += [HaloTicket.state == 'ready', HaloTicket.ticket_id.is_(None)]
    values = dict(lease_token=token, lease_until=moment + timedelta(minutes=2), last_attempt_at=moment,
                  revision=HaloTicket.revision + 1)
    if creating:
        values['state'] = 'creating'
    # A committed compare-and-set reserves the operation without holding a DB lock during HTTP.
    changed = db.session.execute(update(HaloTicket).where(*conditions).values(**values),
                                 execution_options={'synchronize_session': False}).rowcount
    _commit()
    if changed != 1:
        raise HaloBusy('Another operation is processing this ticket. Wait two minutes before retrying.')
    return token


def _owned(link_id, token):
    db.session.expire_all()
    link = db.session.get(HaloTicket, link_id)
    if link.lease_token != token:
        raise HaloBusy('A newer sync is processing this ticket. Reload to see its result.')
    return link


def _release(link):
    link.lease_token = link.lease_until = None


def _failure(link_id, token, error, actor, *, creating=False):
    link = _owned(link_id, token)
    if creating:
        link.state = 'uncertain' if error.ambiguous else 'ready'
    link.failure_count += 1
    delay = max(error.retry_after, min(3600, 60 * (2 ** min(link.failure_count - 1, 6))))
    link.last_error = str(error)[:500]
    link.next_sync_at = now() + timedelta(seconds=delay)
    _release(link)
    audit(actor, 'assessment', link.risk.assessment_id, 'halo operation failed',
          {'risk_id': link.risk_id, 'ticket_id': link.ticket_id, 'state': link.state, 'error': link.last_error})
    _commit()


def _backoff(link):
    if link.last_error and link.next_sync_at and link.next_sync_at > now():
        raise HaloBusy(f'Retry this ticket after {link.next_sync_at:%Y-%m-%d %H:%M:%S} UTC.')


def create_ticket(risk, actor):
    config = HaloConfig.load()
    link = risk.halo_ticket
    if link:
        _same_instance(link, config)
        if link.ticket_id:
            return link, False
        if link.state != 'ready':
            raise HaloError('Halo may already have created this ticket. Use ticket recovery before trying again.')
        _backoff(link)
    check_action(risk)
    if not link:
        correlation = str(uuid4())
        link = HaloTicket(risk_id=risk.id, created_by=actor, api_url=config.api_url,
                          ticket_url_template=config.ticket_url_template, correlation=correlation,
                          action_context=action_context(risk), request_payload=make_payload(risk, config, correlation))
        db.session.add(link)
        try:
            _commit()
        except IntegrityError:
            db.session.rollback()
            raise HaloBusy('Another request already prepared this action. Reload before trying again.') from None
    token = _claim(link, creating=True)
    link_id = link.id
    # A known rejected attempt can use corrected routing or action details on retry.
    link = _owned(link_id, token)
    link.action_context = action_context(link.risk)
    link.request_payload = make_payload(link.risk, config, link.correlation)
    payload = link.request_payload
    audit(actor, 'assessment', link.risk.assessment_id, 'halo ticket creation requested',
          {'risk_id': link.risk_id, 'reference': link.correlation})
    _commit()
    try:
        ticket_id = HaloClient(config).create_ticket(payload)
    except HaloError as error:
        _failure(link_id, token, error, actor, creating=True)
        raise
    link = _owned(link_id, token)
    link.ticket_id, link.state = ticket_id, 'linked'
    link.last_error, link.failure_count, link.next_sync_at = '', 0, None
    _release(link)
    audit(actor, 'assessment', link.risk.assessment_id, 'halo ticket created',
          {'risk_id': link.risk_id, 'ticket_id': ticket_id})
    try:
        _commit()
    except IntegrityError:
        db.session.rollback()
        raise HaloError('Halo returned an ID already linked to another action. Review the ticket before recovery.') from None
    return link, True


def progress_from(ticket, note, config):
    status_id = identifier(ticket['status_id'])
    return {'status_id': status_id, 'status': clean_text(ticket.get('status_name')) or f'Status #{status_id}',
            'completion_reported': status_id in config.completed_status_ids,
            'team': clean_text(ticket.get('team')), 'assignee': clean_text(ticket.get('agent_name')),
            'target_date': clean_text(ticket.get('targetdate'), 80),
            'updated_at': clean_text(ticket.get('last_update') or ticket.get('lastactiondate'), 80),
            'closed_at': clean_text(ticket.get('dateclosed'), 80), 'public_note': note}


def sync_ticket(link, actor=None, client=None):
    config = client.config if client else HaloConfig.load()
    _same_instance(link, config)
    if not link.ticket_id or link.state != 'linked':
        raise HaloError('Recover or create the Halo ticket before syncing progress.')
    _backoff(link)
    token = _claim(link)
    link_id, ticket_id = link.id, link.ticket_id
    client = client or HaloClient(config)
    # End read transactions before the remote requests, including in a long-running CLI batch.
    _commit()
    try:
        ticket = client.get_ticket(ticket_id)
        progress = progress_from(ticket, client.public_note(ticket_id), config)
    except HaloError as error:
        _failure(link_id, token, error, actor)
        raise
    link = _owned(link_id, token)
    if link.progress and link.progress.get('completion_reported') and not progress['completion_reported']:
        link.reopened = True
    if progress['completion_reported']:
        link.reopened = False
    if link.progress != progress:
        audit(actor, 'assessment', link.risk.assessment_id, 'halo progress updated',
              {'risk_id': link.risk_id, 'ticket_id': ticket_id, 'progress': progress, 'reopened': link.reopened})
    link.progress = progress
    link.last_synced_at, link.next_sync_at = now(), now() + timedelta(minutes=5)
    link.last_error, link.failure_count = '', 0
    _release(link)
    _commit()
    return link


def recover_ticket(link, ticket_id, actor):
    config = HaloConfig.load()
    _same_instance(link, config)
    if link.ticket_id or link.state not in ('creating', 'uncertain'):
        raise HaloError('Recovery is only available for an unconfirmed ticket creation.')
    ticket_id = identifier(ticket_id)
    token = _claim(link)
    link_id = link.id
    _commit()
    try:
        ticket = HaloClient(config).get_ticket(ticket_id)
        marker = f'[AIrisk:{link.correlation}]'
        if marker not in str(ticket.get('summary', '')) and marker not in str(ticket.get('details', '')):
            raise HaloError('That Halo ticket does not contain this action’s unique AIrisk reference.')
    except HaloError as error:
        _failure(link_id, token, error, actor)
        raise
    link = _owned(link_id, token)
    link.ticket_id, link.state = ticket_id, 'linked'
    link.last_error, link.failure_count, link.next_sync_at = '', 0, None
    _release(link)
    audit(actor, 'assessment', link.risk.assessment_id, 'halo ticket recovered',
          {'risk_id': link.risk_id, 'ticket_id': ticket_id})
    try:
        _commit()
    except IntegrityError:
        db.session.rollback()
        raise HaloError('This Halo ticket is already linked to another action.') from None
    return link


def reset_creation(link, actor):
    if link.ticket_id or link.state not in ('creating', 'uncertain'):
        raise HaloError('Only an unconfirmed creation can be reset.')
    token = _claim(link)
    link = _owned(link.id, token)
    link.state, link.last_error, link.next_sync_at = 'ready', '', None
    _release(link)
    audit(actor, 'assessment', link.risk.assessment_id, 'halo creation reset after manual absence check',
          {'risk_id': link.risk_id, 'reference': link.correlation})
    _commit()


def sync_due(limit=50):
    client = HaloClient(HaloConfig.load())
    ids = db.session.scalars(select(HaloTicket.id).where(
        HaloTicket.state == 'linked',
        or_(HaloTicket.next_sync_at.is_(None), HaloTicket.next_sync_at <= now())
    ).order_by(HaloTicket.next_sync_at, HaloTicket.id).limit(limit)).all()
    _commit()
    result = {'synced': 0, 'skipped': 0, 'failed': 0}
    for link_id in ids:
        try:
            sync_ticket(db.session.get(HaloTicket, link_id), client=client)
            result['synced'] += 1
        except HaloBusy:
            db.session.rollback()
            result['skipped'] += 1
        except HaloError:
            db.session.rollback()
            result['failed'] += 1
    return result
