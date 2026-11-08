"""Authenticated controls for enterprise intake, handoff and progress."""
import click
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .enterprise_api import EnterpriseConfig, PROVIDERS
from .integration_http import IntegrationError
from . import enterprise_workflow as workflow
from .models import db, Assessment, ExternalLink
from .web import number, signed_in, text

bp = Blueprint('integrations', __name__)


@bp.app_context_processor
def context():
    return {'integration_can_manage': workflow.can_manage, 'integration_can_handoff': workflow.can_handoff,
            'coupa_source_changed': workflow.source_changed, 'integration_names': PROVIDERS,
            'integration_labels': {'status': 'Status', 'name': 'Assessment', 'resultName': 'Result',
                'deadline': 'Deadline', 'lastUpdated': 'Last updated', 'completedOn': 'Completed on',
                'totalRiskCount': 'Total risks', 'openRiskCount': 'Open risks', 'highRisk': 'High risks',
                'veryHighRisk': 'Very high risks', 'requisition_id': 'Requisition', 'line_id': 'Line',
                'supplier_id': 'Supplier ID', 'supplier': 'Supplier', 'product': 'Product', 'updated_at': 'Last updated'}}


@bp.get('/integrations')
@signed_in
def index():
    configs = {}
    for provider, label in PROVIDERS.items():
        try:
            config = EnterpriseConfig.load(provider)
            configs[provider] = {'name': label, 'ready': True, 'api_url': config.api_url}
        except IntegrationError as error:
            configs[provider] = {'name': label, 'ready': False,
                                 'problem': str(error) if g.user.role == 'admin' else 'Ask an administrator to configure this connection.'}
    links = db.session.scalars(select(ExternalLink).order_by(ExternalLink.id.desc()).limit(100)).all()
    return render_template('integrations.html', configs=configs, links=links)


@bp.post('/integrations/coupa/import')
@signed_in
def coupa_import():
    try:
        link = workflow.intake(number('requisition_id', True), number('line_id', True), g.user.id)
        flash('Coupa line imported. Complete its AI context before assessing.', 'success')
        return redirect(url_for('integrations.detail', link_id=link.id))
    except IntegrationError as error:
        flash(str(error), 'error')
    return redirect(url_for('integrations.index'))


@bp.post('/assessments/<int:assessment_id>/onetrust')
@signed_in
def onetrust(assessment_id):
    assessment = db.get_or_404(Assessment, assessment_id)
    if not workflow.can_handoff(assessment, g.user):
        abort(403)
    try:
        if request.form.get('operation') == 'launch':
            link = workflow.launch(assessment, g.user.id)
        elif request.form.get('operation') == 'link':
            link = workflow.connect(assessment, text('external_id', 80, True), g.user.id)
        else:
            abort(400)
        return redirect(url_for('integrations.detail', link_id=link.id))
    except IntegrationError as error:
        flash(str(error), 'error')
    return redirect(url_for('web.assessment', assessment_id=assessment_id))


@bp.route('/integrations/records/<int:link_id>', methods=['GET', 'POST'])
@signed_in
def detail(link_id):
    link = db.get_or_404(ExternalLink, link_id)
    assessment = link.assessment or (link.system.assessments[0] if link.system and link.system.assessments else None)
    if request.method == 'POST':
        operation = request.form.get('operation')
        if not workflow.can_manage(link, g.user) and not (g.user.role == 'reviewer' and operation == 'sync'):
            abort(403)
        if number('revision', True) != link.revision:
            abort(409, description='This integration record changed. Reload before trying again.')
        try:
            if operation == 'sync':
                workflow.sync(link, g.user.id)
                flash('External progress updated.', 'success')
            elif operation == 'publish' and assessment:
                workflow.publish(link, assessment, g.user.id, text('digest', 64, True))
                flash('Summary publication ' + link.publication_state + '.', 'success')
            elif operation == 'reset' and g.user.role == 'admin' and request.form.get('confirmed_absent') == 'yes':
                workflow.reset_launch(link, g.user.id)
                flash('Launch reset after confirming absence in OneTrust.', 'success')
            else:
                abort(400)
        except IntegrationError as error:
            flash(str(error), 'error')
        return redirect(url_for('integrations.detail', link_id=link.id))
    config, problem, summary, target = None, None, None, None
    try:
        config = workflow.config_for(link)
        if assessment:
            summary = workflow.summary_for(assessment, link, config.public_url)
        target = config.write_target()
    except IntegrationError as error:
        problem = str(error)
    return render_template('integration_detail.html', link=link, assessment=assessment, summary=summary,
                           problem=problem, target=target, manageable=workflow.can_manage(link, g.user))


def register_commands(app):
    @app.cli.command('integrations-sync')
    @click.option('--limit', type=click.IntRange(1, 200), default=50, show_default=True)
    def integrations_sync(limit):
        """Read external progress once for HaloITSM, OneTrust and Coupa."""
        from .halo import HaloConfig, HaloError
        from .halo_workflow import sync_due as halo_sync
        from .models import HaloTicket
        result = workflow.sync_due(limit)
        click.echo(f'OneTrust/Coupa: {result["synced"]} synced, {result["busy"]} busy, {result["failed"]} failed.')
        failed = result['failed']
        if db.session.scalar(select(HaloTicket.id).limit(1)):
            try:
                HaloConfig.load()
                halo = halo_sync(limit)
                click.echo(f'HaloITSM: {halo["synced"]} synced, {halo["skipped"]} busy, {halo["failed"]} failed.')
                failed += halo['failed']
            except HaloError as error:
                click.echo(str(error))
                failed += 1
        if failed:
            raise click.ClickException('Some records could not sync. Review integration configuration and recorded errors.')
