"""OneTrust assessment and Coupa requisition adapters; no approval endpoints."""
from dataclasses import dataclass, field
import os
import re
from uuid import UUID

from flask import current_app

from .halo import clean_text, https_url, identifier
from .integration_http import IntegrationError, OAuthClient

PROVIDERS = {'onetrust': 'OneTrust', 'coupa': 'Coupa'}
COMMON = ('API_URL', 'TOKEN_URL', 'CLIENT_ID', 'CLIENT_SECRET', 'SCOPE', 'RECORD_URL_TEMPLATE')
EXTRA = {'onetrust': ('TEMPLATE_ID', 'RESPONDENT_ID', 'RESPONDENT_NAME', 'ORG_GROUP_ID',
                      'SECTION_ID', 'QUESTION_ID'),
         'coupa': ('STATUS_FIELD', 'SUMMARY_FIELD', 'URL_FIELD')}


def environment():
    return {f'{p.upper()}_{key}': os.environ.get(f'{p.upper()}_{key}', '')
            for p in PROVIDERS for key in COMMON + EXTRA[p]}


def uuid_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise IntegrationError('Expected a valid UUID.') from None


@dataclass(frozen=True)
class EnterpriseConfig:
    provider: str
    api_url: str
    token_url: str
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    scope: str
    url_template: str
    options: dict
    public_url: str = ''

    @classmethod
    def load(cls, provider):
        if provider not in PROVIDERS:
            raise IntegrationError('Unknown integration.')
        prefix, app = provider.upper() + '_', current_app.config
        required = COMMON
        missing = [prefix + k for k in required if not app.get(prefix + k)]
        if missing:
            raise IntegrationError(PROVIDERS[provider] + ' is not configured. Missing: ' + ', '.join(missing) + '.')
        api = https_url(str(app[prefix + 'API_URL']).strip(), prefix + 'API_URL')
        token = https_url(str(app[prefix + 'TOKEN_URL']).strip(), prefix + 'TOKEN_URL', query=True)
        template = str(app[prefix + 'RECORD_URL_TEMPLATE']).strip()
        if template.count('{id}') != 1 or any(c in template.replace('{id}', '') for c in '{}'):
            raise IntegrationError(prefix + 'RECORD_URL_TEMPLATE must contain exactly one {id} placeholder.')
        https_url(template.replace('{id}', '1'), prefix + 'RECORD_URL_TEMPLATE', query=True)
        options = {k: str(app.get(prefix + k, '')).strip() for k in EXTRA[provider]}
        if provider == 'onetrust':
            for key, value in options.items():
                if key.endswith('_ID') and value:
                    options[key] = uuid_id(value)
            if len(options['RESPONDENT_NAME']) > 200:
                raise IntegrationError('ONETRUST_RESPONDENT_NAME must fit within 200 characters.')
        else:
            values = [v for v in options.values() if v]
            if (any(not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}', v) for v in values)
                    or len(set(values)) != len(values)):
                raise IntegrationError('Coupa custom-field API names must be distinct lowercase names.')
        public = str(app.get('AIRISK_PUBLIC_URL', '')).strip()
        if public:
            public = https_url(public, 'AIRISK_PUBLIC_URL')
        return cls(provider, api, token, str(app[prefix + 'CLIENT_ID']), str(app[prefix + 'CLIENT_SECRET']),
                   str(app[prefix + 'SCOPE']), template, options, public)

    def write_target(self):
        keys = ('SECTION_ID', 'QUESTION_ID') if self.provider == 'onetrust' else EXTRA['coupa']
        if not all(self.options[k] for k in keys):
            raise IntegrationError('Configure the dedicated summary destination before publishing.')
        return {k: self.options[k] for k in keys}


class OneTrustClient(OAuthClient):
    service = 'OneTrust'

    def launch(self, payload):
        result = self.call('/api/assessment/v3/assessments', payload=payload)
        try:
            return uuid_id(result['assessmentId'])
        except (KeyError, TypeError, IntegrationError):
            raise IntegrationError('OneTrust did not confirm an assessment ID. Recover the assessment before retrying.',
                                   ambiguous=True) from None

    def get(self, external_id):
        external_id = uuid_id(external_id)
        result = self.call(f'/api/assessment/v2/assessments/{external_id}/export')
        if (not isinstance(result, dict) or result.get('assessmentId') != external_id
                or not isinstance(result.get('status'), str) or not result['status']):
            raise IntegrationError('OneTrust returned an unexpected assessment ID or missing status.')
        return result

    def progress(self, record):
        result = {k: clean_text(record.get(k), 250) for k in
                  ('status', 'name', 'resultName', 'deadline', 'lastUpdated', 'completedOn')}
        for key in ('totalRiskCount', 'openRiskCount', 'highRisk', 'veryHighRisk'):
            value = record.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[key] = value
        return result

    def summary_text(self, record, target, *, editable=False):
        """Find exactly the reserved plain-text question; never overwrite another answer."""
        matches = []
        try:
            for section in record['sections']:
                section_id = section.get('sectionId') or section.get('header', {}).get('sectionId')
                if section_id != target['SECTION_ID']:
                    continue
                for entry in section['questions']:
                    question = entry['question']
                    if question.get('id') == target['QUESTION_ID']:
                        if ((question.get('questionType') or question.get('type')) != 'TEXTBOX'
                                or (editable and (entry.get('hidden') or entry.get('lockReason')))):
                            raise IntegrationError('The OneTrust summary question must be an editable TEXTBOX.')
                        responses = entry.get('questionResponses', [])
                        texts = [r['response'] for group in responses for r in group.get('responses', [])
                                 if r.get('response')]
                        if len(texts) > 1 or any(not isinstance(t, str) for t in texts):
                            raise ValueError()
                        matches.append(texts[0] if texts else '')
            if len(matches) != 1:
                raise ValueError()
        except (TypeError, KeyError, ValueError, AttributeError):
            raise IntegrationError('OneTrust did not return the configured summary question in the expected format.') from None
        return matches[0]

    def publish(self, external_id, target, summary):
        return self.call(f'/api/assessment/v2/assessments/{uuid_id(external_id)}/responses', payload=[{
            'assessmentId': external_id, 'sectionId': target['SECTION_ID'], 'questionId': target['QUESTION_ID'],
            'responses': [{'type': 'DEFAULT', 'response': summary['text']}]}])


class CoupaClient(OAuthClient):
    service = 'Coupa'

    def get(self, external_id):
        try:
            req_id, line_id = (identifier(v) for v in external_id.split(':'))
        except (ValueError, AttributeError):
            raise IntegrationError('Choose positive requisition and requisition line IDs.') from None
        result = self.call(f'/requisitions/{req_id}')
        try:
            if identifier(result['id']) != req_id or not isinstance(result['status'], str):
                raise ValueError()
            lines = [line for line in result['requisition-lines'] if identifier(line['id']) == line_id]
            if len(lines) != 1:
                raise ValueError()
            line = lines[0]
            supplier_id = identifier(line['supplier']['id'])
            supplier = self.call(f'/suppliers/{supplier_id}')
            if identifier(supplier['id']) != supplier_id:
                raise ValueError()
            name, product = clean_text(supplier['name'], 255), clean_text(line['description'], 255)
            fields = line.get('custom-fields', {})
            if not name or not product or not isinstance(fields, dict):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise IntegrationError('Coupa returned an unexpected requisition, line or supplier.') from None
        return {'requisition_id': req_id, 'line_id': line_id, 'supplier_id': supplier_id,
                'supplier': name, 'product': product, 'status': clean_text(result['status']),
                'updated_at': clean_text(result.get('updated-at'), 80), 'fields': fields}

    def progress(self, record):
        return {k: v for k, v in record.items() if k != 'fields'}

    def summary_fields(self, record, target):
        return {k: record['fields'].get(v) or '' for k, v in target.items()}

    def publish(self, external_id, target, summary):
        req_id, line_id = (identifier(v) for v in external_id.split(':'))
        fields = {target['STATUS_FIELD']: summary['status'], target['SUMMARY_FIELD']: summary['text'],
                  target['URL_FIELD']: summary['url']}
        return self.call(f'/requisitions/{req_id}', method='PUT',
                         payload={'requisition-lines': [{'id': line_id, 'custom-fields': fields}]})


def client_for(config):
    return (OneTrustClient if config.provider == 'onetrust' else CoupaClient)(config)
