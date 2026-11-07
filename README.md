# AIrisk — AI adoption workspace

AIrisk helps an organization decide whether, and under what conditions, to adopt an AI product or internal use case. It connects the system inventory, vendor facts, AI risk scenarios, supporting evidence, accountable actions and an independent decision.

The application is a self-hosted workspace for **one organization**. All active members can read that organization's records. It is suitable for a small internal pilot with a named owner and a separate reviewer. It does not automatically determine legal compliance or test an AI model for you.

## Start locally

Use Python 3.11 or 3.12 on Linux, macOS or Windows through WSL. From the repository directory:

```bash
bash run-local.sh
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000). The first visit lets you create an administrator account. Then add a separate reviewer from **Team**. There are no default credentials. The launcher installs the Python dependencies, applies database migrations and starts Gunicorn bound to localhost.

Data is stored in `instance/airisk.db` and survives restarts. The local signing key is generated once in `instance/secret.key`. Both are ignored by Git. Stop the server with Ctrl+C.

To explore three clearly labeled fictional examples after creating your account:

```bash
.venv/bin/python -m flask --app app seed-demo --owner your-email@example.com
```

## Complete a real assessment

1. Add the vendor product, retention/training terms, processing regions, contract notes and next review date. Record dated evidence from your document store or test results.
2. Add an AI use case: purpose, affected people, accountable owner, model/version, data, retrieval, tool authority and EU scope/role.
3. Start an assessment. The application selects relevant scenarios from its 16-risk starter catalog. Add any organization-specific AI scenarios identified through consultation or testing.
4. Record inherent and residual likelihood/impact, the reasoning, controls actually operating, and linked evidence. Unknown values remain unassessed. Justify exclusions individually. Assign an owner and due date to each action.
5. Request independent review. A different reviewer verifies evidence, records legal applicability, and approves, approves with conditions, or rejects the use case. Missing evidence, unresolved scope, possible prohibited practices and critical residual risks block approval.
6. Export JSON, CSV, or the printable report using your browser's Save as PDF. Monitor review dates, evidence expiry, vendor changes and overdue actions. Conditional actions accept completion evidence and a separate verification. Original decisions remain preserved.

Changed system/vendor context requires a new assessment. Closing an action does not silently lower a risk rating or broaden an authorization. Decided assessments cannot be edited; retain them as the historical record.

## Frameworks and method

The versioned catalog contains original risk scenarios with links to the source guidance. It separates three different functions:

| Input | How it is used |
| --- | --- |
| [NIST AI RMF 1.0](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf) and [Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf) | Governance, use context, evaluation, controls, residual risk and monitoring |
| [OWASP LLM Top 10, 2025](https://genai.owasp.org/llm-top-10/) and [Agentic Applications Top 10, 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | Security scenarios and evidence prompts for LLMs, retrieval and agents |
| [EU AI Act, consolidated 27 July 2026](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02024R1689-20260727) | Conservative applicability screening and independent review of role, prohibited/high-risk uses, duties and dates |

Mappings indicate relevant guidance, not equivalence, exhaustive coverage or certification. The numerical risk bands are an explicit **local prioritization policy**, not a NIST-prescribed formula or EU legal classification. A human reviewer must establish obligations and document the reasoning. The catalog was checked on 7 November 2026 and is due for editorial review by 5 February 2027; new approvals stop when this review becomes overdue.

The [review and roadmap](docs/REVIEW_AND_ROADMAP.md) explains the original problems, product decisions, delivered scope and remaining priorities. See [architecture](ARCHITECTURE.md) and [operations](docs/OPERATIONS.md) for the implementation and deployment boundaries.

## Validation

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m flask --app app db check
```

A real Chromium journey also exercises account setup, vendor registration, a complete assessment, independent review, exports, hiring/agent routing and mobile layout. It uses synthetic business data and a real HTTP server; it does not evaluate an actual supplier's AI. Run it only against a **fresh disposable development database**:

```bash
npm ci
npx playwright install --with-deps chromium
export AIRISK_INSTANCE="$(mktemp -d)"
.venv/bin/python -m flask --app app db upgrade
.venv/bin/gunicorn --bind 127.0.0.1:5057 'app:create_app()'
# In another terminal, from this repository:
npm run test:browser
```

Browser screenshots and a generated PDF are written to ignored `test-results/`. GitHub Actions runs Python 3.11/3.12 workflow tests, migrations, the browser journey and a container startup check.

## Hosted operation

A non-root Gunicorn container, persistent volume and health endpoint are provided. Use [the operations guide](docs/OPERATIONS.md) to configure a strong signing secret, HTTPS, accounts and backups. The production configuration requires HTTPS for session cookies and disables browser-based first-account setup.

This release has local accounts, shared organizational visibility and SQLite storage. SSO/MFA, isolated tenants, evidence file storage, automated notifications, a complete obligation engine and externally tested high availability remain future work. Do not treat the presence of an approval badge as proof that the underlying AI is safe or legally compliant.
