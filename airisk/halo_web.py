"""Session-authenticated Halo controls; all mutations use the existing CSRF boundary."""
import click
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .halo import HaloConfig, HaloError
from .halo_workflow import (can_manage, create_ticket, recover_ticket, reset_creation,
                            sync_due, sync_ticket)
from .models import db, HaloTicket, Risk
from .services import halo_action_changed
from .web import number, signed_in

bp = Blueprint('halo', __name__)


@bp.app_context_processor
def context():
    try:
        HaloConfig.load()
        ready = True
    except HaloError:
        ready = False
    return {'halo_can_manage': can_manage, 'halo_action_changed': halo_action_changed, 'halo_ready': ready}


@bp.get('/integrations/halo')
@signed_in
def settings():
    if g.user.role != 'admin':
        abort(403)
    config, problem = None, None
    try:
        config = HaloConfig.load()
    except HaloError as error:
        problem = str(error)
    links = db.session.scalars(select(HaloTicket).order_by(HaloTicket.id.desc()).limit(100)).all()
    return render_template('halo_settings.html', config=config, problem=problem, links=links)


def action_risk(risk_id, *, syncing=False):
    risk = db.get_or_404(Risk, risk_id)
    if not can_manage(risk, g.user) and not (syncing and g.user.role == 'reviewer'):
        abort(403)
    return risk


def back(risk):
    return redirect(url_for('web.assessment', assessment_id=risk.assessment_id, _anchor=f'risk-{risk.id}'))


@bp.post('/risks/<int:risk_id>/halo/create')
@signed_in
def create(risk_id):
    risk = action_risk(risk_id)
    if number('revision', True) != risk.revision:
        abort(409, description='The action changed. Reload and review its saved details before creating a ticket.')
    try:
        link, created = create_ticket(risk, g.user.id)
        if created:
            flash(f'Halo ticket #{link.ticket_id} created.', 'success')
            sync_ticket(link, g.user.id)
        else:
            flash(f'This action is already linked to Halo ticket #{link.ticket_id}.', 'success')
    except HaloError as error:
        flash(str(error), 'error')
    return back(risk)


@bp.post('/risks/<int:risk_id>/halo/sync')
@signed_in
def sync(risk_id):
    risk = action_risk(risk_id, syncing=True)
    if not risk.halo_ticket:
        abort(404)
    try:
        sync_ticket(risk.halo_ticket, g.user.id)
        flash('Halo progress updated. Review completion evidence separately in AIrisk.', 'success')
    except HaloError as error:
        flash(str(error), 'error')
    return back(risk)


@bp.post('/risks/<int:risk_id>/halo/recover')
@signed_in
def recover(risk_id):
    risk = action_risk(risk_id)
    if not risk.halo_ticket:
        abort(404)
    ticket_id = number('ticket_id', True)
    try:
        link = recover_ticket(risk.halo_ticket, ticket_id, g.user.id)
        flash(f'Halo ticket #{link.ticket_id} recovered.', 'success')
        sync_ticket(link, g.user.id)
    except (HaloError, ValueError) as error:
        flash(str(error), 'error')
    return back(risk)


@bp.post('/risks/<int:risk_id>/halo/reset')
@signed_in
def reset(risk_id):
    if g.user.role != 'admin':
        abort(403)
    risk = action_risk(risk_id)
    if not risk.halo_ticket:
        abort(404)
    if request.form.get('confirmed_absent') != 'yes':
        abort(400, description='Confirm you searched Halo for the unique reference and found no ticket.')
    try:
        reset_creation(risk.halo_ticket, g.user.id)
        flash('Creation reset. You can now create a ticket from the saved action.', 'success')
    except HaloError as error:
        flash(str(error), 'error')
    return back(risk)


def register_commands(app):
    @app.cli.command('halo-sync')
    @click.option('--limit', type=click.IntRange(1, 200), default=50, show_default=True)
    def halo_sync(limit):
        """Poll due linked tickets once; schedule this command every five minutes."""
        try:
            result = sync_due(limit)
        except HaloError as error:
            raise click.ClickException(str(error)) from None
        click.echo(f'Halo sync: {result["synced"]} synced, {result["skipped"]} busy, {result["failed"]} failed.')
        if result['failed']:
            raise click.ClickException('Some tickets could not sync. Review their recorded errors in AIrisk.')
