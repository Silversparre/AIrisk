"""Bounded Halo REST client. Credentials and tokens never enter the database."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import os
from urllib.parse import urlsplit
from urllib.request import build_opener
from .integration_http import IntegrationError, OAuthClient, NoRedirects, MAX_RESPONSE, retry_delay

from flask import current_app

ENV_KEYS = ('API_URL', 'TOKEN_URL', 'CLIENT_ID', 'CLIENT_SECRET', 'TICKET_URL_TEMPLATE',
            'TICKET_TYPE_ID', 'TEAM', 'AGENT_ID', 'CUSTOMER_ID', 'SITE_ID', 'USER_ID',
            'PRIORITY_ID', 'COMPLETED_STATUS_IDS', 'SCOPE')
REQUIRED = ('API_URL', 'TOKEN_URL', 'CLIENT_ID', 'CLIENT_SECRET', 'TICKET_URL_TEMPLATE', 'TICKET_TYPE_ID')
MAX_RESPONSE = 2 * 1024 * 1024


class HaloError(IntegrationError):
    pass


def environment():
    return {**{'HALO_' + key: os.environ.get('HALO_' + key, '') for key in ENV_KEYS},
            'AIRISK_PUBLIC_URL': os.environ.get('AIRISK_PUBLIC_URL', '')}


def identifier(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError('Expected a positive integer ID.')
    value = int(value)
    if not 0 < value <= 2147483647:
        raise ValueError('Expected a positive integer ID.')
    return value


def https_url(value, name, *, query=False):
    try:
        parsed = urlsplit(value)
        if (not value or len(value) > 1000 or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment
                or (parsed.query and not query) or any(c.isspace() for c in value)):
            raise ValueError()
        parsed.port  # Reject malformed ports before passing URLs to the HTTP client.
    except ValueError:
        raise HaloError(f'{name} must be an HTTPS URL without credentials or a fragment.') from None
    return value.rstrip('/')


@dataclass(frozen=True)
class HaloConfig:
    api_url: str
    token_url: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    ticket_url_template: str
    defaults: dict
    completed_status_ids: frozenset
    public_url: str = ''
    scope: str = 'read:tickets edit:tickets'

    @classmethod
    def load(cls):
        config = current_app.config
        missing = ['HALO_' + k for k in REQUIRED if not config.get('HALO_' + k)]
        if missing:
            raise HaloError('HaloITSM is not configured. Missing: ' + ', '.join(missing) + '.')
        api = https_url(str(config['HALO_API_URL']).strip(), 'HALO_API_URL')
        token = https_url(str(config['HALO_TOKEN_URL']).strip(), 'HALO_TOKEN_URL', query=True)
        template = str(config['HALO_TICKET_URL_TEMPLATE']).strip()
        if template.count('{id}') != 1 or '{' in template.replace('{id}', '') or '}' in template.replace('{id}', ''):
            raise HaloError('HALO_TICKET_URL_TEMPLATE must contain exactly one {id} placeholder.')
        https_url(template.replace('{id}', '1'), 'HALO_TICKET_URL_TEMPLATE', query=True)
        defaults = {}
        for key, remote in (('TICKET_TYPE_ID', 'tickettype_id'), ('AGENT_ID', 'agent_id'),
                            ('CUSTOMER_ID', 'client_id'), ('SITE_ID', 'site_id'),
                            ('USER_ID', 'user_id'), ('PRIORITY_ID', 'priority_id')):
            if config.get('HALO_' + key):
                try:
                    defaults[remote] = identifier(config['HALO_' + key])
                except ValueError:
                    raise HaloError(f'HALO_{key} must be a positive integer ID.') from None
        team = str(config.get('HALO_TEAM', '')).strip()
        if len(team) > 200:
            raise HaloError('HALO_TEAM must fit within 200 characters.')
        if team:
            defaults['team'] = team
        try:
            completed = frozenset(identifier(v.strip()) for v in
                                  str(config.get('HALO_COMPLETED_STATUS_IDS', '')).split(',') if v.strip())
        except ValueError:
            raise HaloError('HALO_COMPLETED_STATUS_IDS must be comma-separated positive integer IDs.') from None
        public = str(config.get('AIRISK_PUBLIC_URL', '')).strip()
        if public:
            public = https_url(public, 'AIRISK_PUBLIC_URL')
        return cls(api, token, str(config['HALO_CLIENT_ID']), str(config['HALO_CLIENT_SECRET']),
                   template, defaults, completed, public,
                   str(config.get('HALO_SCOPE') or 'read:tickets edit:tickets'))


class HaloClient(OAuthClient):
    service = 'Halo'
    error_type = HaloError

    def __init__(self, config):
        super().__init__(config, opener=build_opener(NoRedirects()))

    def create_ticket(self, payload):
        result = self.call('/Tickets', payload=[payload])
        try:
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
            return identifier(result['id'])
        except (TypeError, KeyError, ValueError):
            raise HaloError('Halo did not confirm a valid ticket ID. Recover the ticket before trying again.',
                            ambiguous=True) from None

    def get_ticket(self, ticket_id):
        result = self.call(f'/Tickets/{identifier(ticket_id)}', query={'includeagent': 'true'})
        try:
            if not isinstance(result, dict) or identifier(result.get('id')) != ticket_id:
                raise ValueError()
            identifier(result['status_id'])
        except (KeyError, ValueError):
            raise HaloError('Halo returned a ticket with an unexpected ID or missing status.') from None
        return result

    def public_note(self, ticket_id):
        result = self.call('/Actions', query={'ticket_id': ticket_id, 'count': 1,
                           'excludeprivate': 'true', 'excludesys': 'true', 'includehtmlnote': 'false'})
        actions = result.get('actions', result.get('actionsdetails')) if isinstance(result, dict) else result
        if not isinstance(actions, list):
            raise HaloError('Halo returned an invalid action list.')
        if not actions:
            return None
        action = actions[0]
        if not isinstance(action, dict):
            raise HaloError('Halo returned an invalid action.')
        if action.get('hiddenfromuser') or action.get('ticket_id') != ticket_id:
            return None
        try:
            action_id = identifier(action['id'])
        except (KeyError, ValueError):
            raise HaloError('Halo returned an action without a valid ID.') from None
        return {'id': action_id, 'date': clean_text(action.get('datetime'), 80),
                'text': clean_text(action.get('note'), 4000)}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(value, limit=200):
    if not isinstance(value, str):
        return ''
    parser = PlainText()
    parser.feed(value[:20000])
    return ''.join(parser.parts).strip()[:limit]
