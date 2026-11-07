"""Versioned editorial content. Mappings are aids to review, never equivalence claims."""
VERSION = '2026-11-07.1'
REVIEWED_ON = '2026-11-07'
REVIEW_BY = '2027-02-05'
SOURCES = {
    'nist': {'title': 'NIST AI RMF 1.0', 'version': 'January 2023', 'kind': 'Voluntary framework', 'url': 'https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf'},
    'genai': {'title': 'NIST Generative AI Profile', 'version': 'NIST AI 600-1, July 2024', 'kind': 'Voluntary guidance', 'url': 'https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf'},
    'owasp': {'title': 'OWASP LLM Top 10', 'version': '2025 edition', 'kind': 'Security guidance', 'url': 'https://genai.owasp.org/llm-top-10/'},
    'agentic': {'title': 'OWASP Agentic Applications Top 10', 'version': '2026 edition', 'kind': 'Security guidance', 'url': 'https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/'},
    'eu': {'title': 'EU AI Act', 'version': 'Consolidated 27 July 2026', 'kind': 'Law; scope and dates matter', 'url': 'https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02024R1689-20260727'},
    'timeline': {'title': 'EU implementation timetable', 'version': 'Checked 7 November 2026', 'kind': 'Commission implementation guidance', 'url': 'https://ai-act-service-desk.ec.europa.eu/en/ai-act/eu-ai-act-implementation-timeline'},
}
OPTIONS = {
    'ai_type': [('generative', 'Generative AI'), ('predictive', 'Predictive or classification AI'), ('agentic', 'AI agent with tools')],
    'lifecycle': [('proposed', 'Proposed'), ('pilot', 'Pilot'), ('live', 'In use'), ('retired', 'Retired')],
    'data_sensitivity': [('public', 'Public'), ('internal', 'Internal'), ('confidential', 'Confidential'), ('personal', 'Personal data'), ('special', 'Special category or highly sensitive data')],
    'audience': [('internal', 'Employees'), ('external', 'Customers or the public'), ('both', 'Both')],
    'autonomy': [('advisory', 'Advice only'), ('assisted', 'Actions require human approval'), ('automatic', 'Acts without prior approval')],
    'eu_scope': [('yes', 'EU use or EU affected people'), ('no', 'No EU connection identified'), ('unknown', 'Needs investigation')],
    'operator_role': [('deployer', 'Use a vendor system as instructed'), ('provider', 'Develop or supply our own system'), ('both', 'Both provider and deployer'), ('unknown', 'Not yet established')],
    'use_domain': [('general', 'General business support'), ('employment', 'Recruitment or worker decisions'), ('credit', 'Creditworthiness'), ('insurance', 'Life or health insurance pricing'), ('education', 'Education admission or evaluation'), ('essential', 'Access to essential public services'), ('biometric', 'Biometrics or emotion recognition'), ('infrastructure', 'Critical infrastructure or product safety'), ('public_authority', 'Justice, law enforcement or migration'), ('other_sensitive', 'Another consequential use')],
    'prohibited': [('unknown', 'Not yet screened'), ('none', 'Screened; no indicator identified'), ('suspected', 'Possible prohibited practice')],
    'training_use': [('unknown', 'Unknown'), ('no', 'Contract prohibits training on our data'), ('opt_out', 'Opt-out or conditional'), ('yes', 'May train on our data')],
}


def risk(key, title, scenario, question, control, evidence, applies, *refs):
    return dict(key=key, title=title, scenario=scenario, question=question,
                control=control, expected_evidence=evidence, applies=applies,
                sources=[dict(source=k, reference=ref, **SOURCES[k]) for k, ref in refs])


RISKS = [
    risk('quality', 'Wrong answers and unreliable decisions', 'An apparently plausible result is wrong in the actual business context.', 'What errors would change a decision, and how have you tested for them?', 'Define acceptance criteria, test realistic cases and edge cases, provide a fallback, and review high-impact outputs.', 'A dated evaluation with cases, expected results, observed failures, model version and reviewer.', 'all', ('nist', 'MEASURE 2.5'), ('genai', 'MS-2.5-003')),
    risk('oversight', 'People rely on AI without effective oversight', 'A person accepts an AI recommendation without the time, authority or knowledge to challenge it.', 'Who can recognize an error, stop use and provide an alternative?', 'Assign trained reviewers, test intervention and escalation, and explain limitations to users.', 'Oversight procedure, training record and a worked intervention exercise.', 'all', ('nist', 'MANAGE 2.4'), ('eu', 'Articles 4 and 26, where applicable')),
    risk('vendor', 'Vendor changes invalidate the assessment', 'A supplier changes a model, retention policy or subcontractor without a review of the effect on this use case.', 'What changes must the supplier report and how can you stop or switch service?', 'Record exact product/version, contract terms, incident contacts, change notices and exit arrangements.', 'Current contract clauses, vendor answers, change policy and fallback test.', 'vendor', ('nist', 'GOVERN 6.1; MANAGE 3.1'), ('owasp', 'LLM03:2025')),
    risk('privacy', 'Sensitive information reaches unintended recipients', 'Prompts, outputs or retained logs expose personal or confidential information.', 'What data leaves your control, who can access it and when is it deleted?', 'Minimize data, restrict access, verify retention and training settings, and test deletion.', 'Data flow, retention agreement, access test and deletion record; privacy review when relevant.', 'sensitive', ('nist', 'MEASURE 2.10'), ('owasp', 'LLM02:2025')),
    risk('bias', 'Unequal or discriminatory outcomes', 'The system performs differently for affected groups or reinforces harmful assumptions.', 'Which people could be disadvantaged, and what evidence shows how the system performs for them?', 'Test relevant groups and accessibility needs, investigate disparities, provide human reconsideration.', 'Representative evaluation, limitations, affected stakeholder feedback and an appeal process.', 'all', ('nist', 'MEASURE 2.11')),
    risk('injection', 'Instructions hidden in untrusted content', 'A message, document or tool result redirects the AI into revealing information or taking unintended action.', 'Can untrusted content influence privileged instructions, data access or tools?', 'Separate data from authority, enforce permissions outside the model and test adversarial documents.', 'Direct and indirect injection tests against the deployed configuration, with failures and limits.', 'generative', ('owasp', 'LLM01:2025')),
    risk('retrieval', 'Retrieval exposes restricted or stale documents', 'An assistant retrieves records the requesting person cannot access, or treats obsolete content as current.', 'Does retrieval enforce each user’s access rights and track source freshness?', 'Apply authorization before retrieval, isolate indexes, remove withdrawn records and cite sources.', 'Cross-role retrieval tests, source inventory and index deletion/freshness checks.', 'rag', ('owasp', 'LLM08:2025')),
    risk('output', 'Generated output causes unsafe downstream behavior', 'AI output is executed or displayed without the same validation as any other untrusted input.', 'Where do generated text, queries, code or markup go next?', 'Validate output against allowed formats; escape rendered content and independently authorize execution.', 'Integration tests for malformed output and unsafe generated commands or markup.', 'generative', ('owasp', 'LLM05:2025')),
    risk('agency', 'An agent acts beyond its intended authority', 'An agent sends messages, changes records or spends resources outside the approved purpose.', 'Which actions can it take, using whose identity, and which require approval?', 'Use narrowly scoped tools, per-user authorization, transaction limits, approval and rollback.', 'Tool permission inventory, denied-action tests and rollback/approval records.', 'actions', ('owasp', 'LLM06:2025'), ('agentic', 'Agent goal, tool and identity risks')),
    risk('poisoning', 'Untrusted data changes system behavior', 'Tampered training, reference or feedback data shifts outputs or recommendations.', 'Who can change the data and how are changes verified?', 'Track provenance, restrict write access and test data changes before release.', 'Data lineage, change review and regression evaluation.', 'all', ('owasp', 'LLM04:2025'), ('nist', 'MEASURE 2.7')),
    risk('transparency', 'People misunderstand the AI’s role', 'Users or affected people cannot tell how AI is involved, what its limits are or how to challenge an outcome.', 'What do people see before using the system or receiving an AI-influenced decision?', 'Provide appropriate notices, explain limitations and document an accessible challenge route.', 'Actual notices, explanation examples and accessibility checks for this use case.', 'all', ('nist', 'MEASURE 2.8; MEASURE 2.9'), ('eu', 'Article 50: provider and deployer duties differ')),
    risk('ip', 'Generated content violates rights or obligations', 'Source material or generated content is used beyond permitted rights or contains protected material.', 'Do the input and output uses fit the licenses, contract and intended publication?', 'Review usage rights, keep provenance and review sensitive external publications.', 'License/contract review and content review examples.', 'generative', ('genai', 'Intellectual Property; Value Chain and Component Integration')),
    risk('content', 'Harmful or misleading content reaches people', 'The system produces abusive, unsafe or fabricated content that causes harm to its audience.', 'What harmful outputs are plausible for this audience and topic?', 'Set topic limits, evaluate harmful output, provide escalation and review public information.', 'Audience-specific safety evaluation, escalation procedure and incident examples.', 'generative', ('owasp', 'LLM09:2025'), ('genai', 'Information Integrity; dangerous or abusive content')),
    risk('consumption', 'Unbounded use disrupts service or budget', 'Repeated requests or agent loops exhaust the budget or block critical operations.', 'What prevents runaway usage and what happens when limits are reached?', 'Set quotas, timeouts and spending limits; test graceful fallback.', 'Load/cost exercise, enforced budgets, alerts and recovery test.', 'all', ('owasp', 'LLM10:2025'), ('nist', 'MEASURE 2.7')),
    risk('prompt_secrets', 'System instructions expose secrets', 'An internal prompt contains credentials or confidential rules that are revealed by the model.', 'Are secrets or access decisions embedded in prompts?', 'Keep secrets outside prompts and enforce access in application services.', 'Prompt inventory review and leakage tests with synthetic markers.', 'generative', ('owasp', 'LLM07:2025')),
    risk('drift', 'Performance deteriorates after approval', 'Changes in inputs, users or vendor models make the previous evaluation unreliable.', 'What will trigger a reassessment, and who monitors the changes?', 'Define monitoring measures, review dates, incident reporting and a suspension process.', 'Monitoring plan with owner, thresholds, example incidents and review cadence.', 'all', ('nist', 'MANAGE 4.1')),
]


def applicable(definition, context):
    rule = definition['applies']
    return {'all': True, 'vendor': bool(context.get('vendor_id')),
            'generative': context['ai_type'] in ('generative', 'agentic'),
            'rag': context['uses_rag'], 'sensitive': context['data_sensitivity'] != 'public',
            'actions': context['ai_type'] == 'agentic' or context['autonomy'] != 'advisory'}[rule]


def triage(context):
    """Conservative routing only. No legal compliance or exemption determination."""
    if context['eu_scope'] == 'no':
        return dict(label='No EU connection declared', level='neutral', review=True,
                    reason='Confirm territorial scope with a reviewer. Operational AI risks still need assessment.')
    if context['prohibited'] == 'suspected':
        return dict(label='Potential prohibited practice', level='danger', review=True,
                    reason='Stop approval and investigate Article 5 with legal expertise. A risk score cannot override a prohibition.')
    if 'unknown' in (context['eu_scope'], context['prohibited'], context['operator_role']):
        return dict(label='Scope unresolved', level='warning', review=True,
                    reason='Establish EU scope, operator role and prohibited-practice screening before a decision.')
    if context['use_domain'] != 'general':
        return dict(label='Potential high-risk use', level='warning', review=True,
                    reason='Check Article 6, Annex I/III and any documented exception. Human review alone does not create an exemption.')
    return dict(label='No high-risk trigger identified', level='neutral', review=True,
                reason='This preliminary screen does not determine compliance. Check transparency, literacy and role-specific duties.')
