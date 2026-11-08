"""Explicit external writes, durable claims, and read-only scheduled reconciliation."""
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from .enterprise_api import EnterpriseConfig, OneTrustClient, CoupaClient, client_for, uuid_id
from .integration_http import IntegrationError
from .models import db, ExternalLink, Vendor, now
from .services import action_resolved, attention, audit


class IntegrationBusy(IntegrationError):
    pass


def commit():
    try:
        db.session.commit()
    except (StaleDataError, IntegrityError):
        db.session.rollback()
        raise IntegrationBusy('Another operation changed or linked this record. Reload to see its result.') from None


def can_manage(link, user):
    if user.role == 'admin':
        return True
    if link.assessment:
        return user.id in (link.assessment.author_id, link.assessment.system.owner_id)
    return user.id == (link.system.owner_id if link.system else link.created_by)


def can_handoff(assessment, user):
    return user.role == 'admin' or user.id in (assessment.author_id, assessment.system.owner_id)


def source_changed(link):
    if not link or link.provider != 'coupa':
        return False
    remote_changed = link.progress and any(link.source.get(k) != link.progress.get(k)
                                           for k in ('supplier_id', 'supplier', 'product'))
    local_changed = (link.system and link.system.vendor_id != link.vendor_id) or (
        link.vendor and (link.vendor.name != link.source.get('supplier', '')[:120]
                         or link.vendor.product != link.source.get('product', '')[:120]))
    return bool(remote_changed or local_changed)


def link_payload(link):
    if not link:
        return None
    return {'provider': link.provider, 'external_id': link.external_id, 'url': link.remote_url,
            'state': link.state, 'progress': link.progress, 'source_changed': source_changed(link),
            'last_synced_at': link.last_synced_at.isoformat() if link.last_synced_at else None,
            'publication_state': link.publication_state, 'last_error': link.last_error,
            'published_assessment_id': (link.publication or {}).get('assessment_id')}


def claim(link, state=None):
    if link.last_error and link.next_sync_at and link.next_sync_at > now():
        raise IntegrationBusy(f'Retry after {link.next_sync_at:%Y-%m-%d %H:%M:%S} UTC.')
    token = str(uuid4())
    values = dict(lease_token=token, lease_until=now() + timedelta(minutes=3), revision=ExternalLink.revision + 1)
    if state:
        values['state'] = state
    changed = db.session.execute(update(ExternalLink).where(
        ExternalLink.id == link.id, ExternalLink.revision == link.revision,
        or_(ExternalLink.lease_until.is_(None), ExternalLink.lease_until <= now())).values(**values),
        execution_options={'synchronize_session': False}).rowcount
    commit()
    if changed != 1:
        raise IntegrationBusy('Another operation is processing this record. Wait three minutes before retrying.')
    return token


def owned(link_id, token):
    db.session.expire_all()
    link = db.session.get(ExternalLink, link_id)
    if link.lease_token != token:
        raise IntegrationBusy('A newer operation is processing this record. Reload to see its result.')
    return link


def log(link, actor, action, details=None):
    entity, entity_id = ('assessment', link.assessment_id) if link.assessment_id else (
        ('system', link.system_id) if link.system_id else ('integration', link.id))
    audit(actor, entity, entity_id, link.provider + ' ' + action,
          {'link_id': link.id, 'external_id': link.external_id, **(details or {})})


def finish(link):
    link.lease_token = link.lease_until = None
    link.failure_count, link.last_error = 0, ''
    link.next_sync_at = now() + timedelta(minutes=5)


def failure(link_id, token, error, actor, *, creating=False):
    link = owned(link_id, token)
    if creating:
        link.state = 'uncertain' if error.ambiguous else 'ready'
    link.lease_token = link.lease_until = None
    link.last_error = str(error)[:500]
    link.failure_count += 1
    link.next_sync_at = now() + timedelta(seconds=max(error.retry_after, min(3600, 60 * 2 ** min(6, link.failure_count))))
    log(link, actor, 'operation failed', {'error': link.last_error, 'state': link.state})
    commit()


def config_for(link):
    config = EnterpriseConfig.load(link.provider)
    if link.api_url != config.api_url:
        raise IntegrationError('This record belongs to another instance. Restore its original API configuration.')
    return config


def prepare_onetrust(assessment, actor, config):
    link = assessment.onetrust_link
    if not link:
        link = ExternalLink(provider='onetrust', assessment_id=assessment.id, created_by=actor,
                            api_url=config.api_url, url_template=config.url_template, correlation=str(uuid4()))
        db.session.add(link)
        commit()
    config_for(link)
    return link


def launch(assessment, actor):
    config = EnterpriseConfig.load('onetrust')
    options = config.options
    if not all(options[k] for k in ('TEMPLATE_ID', 'RESPONDENT_ID', 'RESPONDENT_NAME')):
        raise IntegrationError('Configure the OneTrust template and respondent before launching an assessment.')
    link = prepare_onetrust(assessment, actor, config)
    if link.external_id:
        return link
    if link.state != 'ready':
        raise IntegrationError('OneTrust may already have created this assessment. Recover it using its UUID.')
    token, link_id = claim(link, 'creating'), link.id
    link = owned(link_id, token)
    payload = {'name': f'[AIrisk:{link.correlation}] Assessment #{assessment.id}: {assessment.snapshot["name"]}',
               'description': f'AIrisk assessment #{assessment.id}. Review and publish its summary from AIrisk.',
               'templateId': options['TEMPLATE_ID'],
               'respondents': [{'respondentId': options['RESPONDENT_ID'], 'respondentName': options['RESPONDENT_NAME']}]}
    if options['ORG_GROUP_ID']:
        payload['orgGroupId'] = options['ORG_GROUP_ID']
    if config.public_url:
        payload['description'] += f' {config.public_url}/assessments/{assessment.id}'
    link.request_payload = payload
    log(link, actor, 'launch requested', {'reference': link.correlation})
    commit()
    try:
        external_id = OneTrustClient(config).launch(payload)
    except IntegrationError as error:
        failure(link_id, token, error, actor, creating=True)
        raise
    link = owned(link_id, token)
    link.external_id, link.state = external_id, 'linked'
    finish(link)
    log(link, actor, 'assessment launched')
    commit()
    return link


def connect(assessment, external_id, actor):
    external_id = uuid_id(external_id)
    config = EnterpriseConfig.load('onetrust')
    link = prepare_onetrust(assessment, actor, config)
    if link.external_id:
        if link.external_id != external_id:
            raise IntegrationError('This AIrisk assessment already has a different OneTrust assessment.')
        return link
    token, link_id = claim(link), link.id
    try:
        client = OneTrustClient(config)
        record = client.get(external_id)
        if link.state in ('creating', 'uncertain') and f'[AIrisk:{link.correlation}]' not in (
                str(record.get('name', '')) + str(record.get('description', ''))):
            raise IntegrationError('The OneTrust assessment does not contain this recovery reference.')
    except IntegrationError as error:
        failure(link_id, token, error, actor)
        raise
    link = owned(link_id, token)
    link.external_id, link.state, link.progress = external_id, 'linked', client.progress(record)
    link.last_synced_at = now()
    finish(link)
    log(link, actor, 'assessment linked')
    commit()
    return link


def reset_launch(link, actor):
    if link.external_id or link.state not in ('creating', 'uncertain'):
        raise IntegrationError('Only an unresolved launch can be reset.')
    token, link_id = claim(link), link.id
    link = owned(link_id, token)
    link.state = 'ready'
    finish(link)
    log(link, actor, 'launch absence confirmed')
    commit()


def intake(requisition_id, line_id, actor):
    config = EnterpriseConfig.load('coupa')
    external_id = f'{requisition_id}:{line_id}'
    existing = db.session.scalar(select(ExternalLink).filter_by(provider='coupa', api_url=config.api_url,
                                                               external_id=external_id))
    if existing:
        return existing
    client = CoupaClient(config)
    record = client.get(external_id)
    # An imported supplier/product starts with unresolved governance facts.
    vendor = Vendor(name=record['supplier'][:120], product=record['product'][:120],
                    notes=f'Imported from Coupa requisition {requisition_id}, line {line_id}; supplier {record["supplier_id"]}.')
    link = ExternalLink(provider='coupa', created_by=actor, api_url=config.api_url, url_template=config.url_template,
                        correlation=str(uuid4()), external_id=external_id, state='linked', vendor=vendor,
                        source=client.progress(record), progress=client.progress(record), last_synced_at=now())
    db.session.add_all([vendor, link])
    try:
        db.session.flush()
        log(link, actor, 'line imported')
        commit()
    except IntegrityError:
        db.session.rollback()
        raise IntegrationBusy('This requisition line was already imported. Reload the intake list.') from None
    return link


def summary_for(assessment, link, public_url):
    signals = attention(assessment)
    state = assessment.state if assessment.state in ('approved', 'conditional', 'rejected') else 'pending'
    status = 'review_required' if signals else state
    url = f'{public_url}/assessments/{assessment.id}' if public_url else ''
    unresolved = sum(bool(r.action) and not action_resolved(r) for r in assessment.risks)
    text = (f'[AIrisk:{link.correlation}]\nAIrisk assessment #{assessment.id}: {assessment.snapshot["name"]}\n'
            f'Current review status: {status}\nRecorded decision: {assessment.state}\n'
            f'Review due: {assessment.review_due or "Not set"}\nOpen remediation actions: {unresolved}\n'
            f'Monitoring: {", ".join(signals) or "No current flags"}\n'
            f'Decision rationale: {(assessment.decision or "Not yet reviewed")[:2000]}\n'
            'This summary applies only to the assessed use case. It does not approve a purchase or certify compliance.')
    if url:
        text += '\nAssessment: ' + url
    result = {'assessment_id': assessment.id, 'status': status, 'text': text, 'url': url}
    result['digest'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def observed(client, record, target, summary):
    if client.config.provider == 'onetrust':
        return client.summary_text(record, target) == summary['text']
    return client.summary_fields(record, target) == {
        'STATUS_FIELD': summary['status'], 'SUMMARY_FIELD': summary['text'], 'URL_FIELD': summary['url']}


def sync(link, actor=None, client=None):
    config = config_for(link)
    if not link.external_id or link.state != 'linked':
        raise IntegrationError('Link or recover the remote record before syncing.')
    client = client or client_for(config)
    token, link_id = claim(link), link.id
    try:
        record = client.get(link.external_id)
        confirmed = bool(link.publication and observed(client, record, link.write_target, link.publication))
    except IntegrationError as error:
        failure(link_id, token, error, actor)
        raise
    link = owned(link_id, token)
    progress = client.progress(record)
    if link.progress != progress:
        log(link, actor, 'progress synced', {'status': progress.get('status')})
    link.progress, link.last_synced_at = progress, now()
    if link.publication:
        previous = link.publication_state
        link.publication_state = 'confirmed' if confirmed else ('changed' if previous == 'confirmed' else previous)
        if confirmed and previous != 'confirmed':
            link.published_at = now()
            log(link, actor, 'summary confirmed')
    finish(link)
    commit()
    return link


def publish(link, assessment, actor, digest):
    config = config_for(link)
    target = config.write_target()
    if not link.external_id or link.state != 'linked':
        raise IntegrationError('Link the remote record before publishing.')
    if link.assessment_id != assessment.id and link.system_id != assessment.system_id:
        raise IntegrationError('This assessment does not belong to the linked record.')
    if link.provider == 'coupa' and assessment.system.assessments[0].id != assessment.id:
        raise IntegrationError('Publish the latest assessment for this Coupa line.')
    if link.write_target and link.write_target != target:
        raise IntegrationError('Restore the original summary field mapping before publishing this record.')
    summary = summary_for(assessment, link, config.public_url)
    if summary['digest'] != digest:
        raise IntegrationError('The review summary changed. Reload and review the new preview before publishing.')
    token, link_id = claim(link), link.id
    client = client_for(config)
    try:
        record = client.get(link.external_id)
        if link.provider == 'onetrust':
            if record['status'] not in ('NOT_STARTED', 'IN_PROGRESS'):
                raise IntegrationError('Publish only while the OneTrust assessment is not started or in progress.')
            current = client.summary_text(record, target, editable=True)
        else:
            fields = client.summary_fields(record, target)
            current = fields['SUMMARY_FIELD']
            previous = link.publication or {}
            allowed_fields = [{'STATUS_FIELD': '', 'SUMMARY_FIELD': '', 'URL_FIELD': ''}]
            for candidate in (previous, summary):
                if candidate:
                    allowed_fields.append({'STATUS_FIELD': candidate['status'],
                                           'SUMMARY_FIELD': candidate['text'], 'URL_FIELD': candidate['url']})
            if fields not in allowed_fields:
                raise IntegrationError('The dedicated Coupa fields contain other data or human edits.')
            if client.progress(record) != link.progress:
                raise IntegrationError('Coupa changed since the last sync. Sync and review the summary again.')
        # Compare exact previous text, not just an ownership marker, to preserve human edits.
        allowed = ('', (link.publication or {}).get('text', ''), summary['text'])
        if current not in allowed:
            raise IntegrationError('The summary destination contains another answer or a human edit. Review it in the remote app.')
        if observed(client, record, target, summary):
            confirmed = True
        else:
            link = owned(link_id, token)
            # Recheck the preview after remote reads; local records can change during HTTP.
            if summary_for(db.session.get(type(assessment), assessment.id), link, config.public_url)['digest'] != digest:
                raise IntegrationError('The review changed during publication. Reload the preview.')
            link.publication, link.write_target, link.publication_state = summary, target, 'pending'
            log(link, actor, 'summary publication requested', {'digest': digest, 'assessment_id': assessment.id})
            commit()
            client.publish(link.external_id, target, summary)
            confirmed = observed(client, client.get(link.external_id), target, summary)
    except IntegrationError as error:
        failure(link_id, token, error, actor)
        raise
    link = owned(link_id, token)
    link.publication, link.write_target = summary, target
    link.publication_state = 'confirmed' if confirmed else 'pending'
    if confirmed:
        link.published_at = now()
    finish(link)
    log(link, actor, 'summary publication checked', {'state': link.publication_state})
    commit()
    return link


def sync_due(limit=50):
    result = {'synced': 0, 'busy': 0, 'failed': 0}
    links = db.session.scalars(select(ExternalLink).where(ExternalLink.state == 'linked',
        or_(ExternalLink.next_sync_at.is_(None), ExternalLink.next_sync_at <= now()))
        .order_by(ExternalLink.next_sync_at, ExternalLink.id).limit(limit)).all()
    clients = {}
    for link in links:
        try:
            if link.provider not in clients:
                clients[link.provider] = client_for(config_for(link))
            sync(link, client=clients[link.provider])
            result['synced'] += 1
        except IntegrationBusy:
            result['busy'] += 1
        except IntegrationError:
            result['failed'] += 1
    return result
