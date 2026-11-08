# AIrisk — AI adoption workspace

AIrisk helps an organization decide whether, and under what conditions, to adopt an AI product or internal use case. It connects the system inventory, vendor facts, AI risk scenarios, supporting evidence, accountable actions and an independent decision.

The application is a self-hosted workspace for **one organization**. All active members can read that organization's records. It is suitable for a small internal pilot with a named owner and a separate reviewer. It does not automatically determine legal compliance or test an AI model for you.

## Start locally

For a Windows installation without a separate Python dependency, use the native package described in [Windows IT review](docs/WINDOWS_IT_REVIEW.md). It includes a portable executable and an MSIX signing path. Review builds are unsigned; IT must approve/sign the chosen distribution before corporate deployment. See the [current security review and fixes](docs/SECURITY_REVIEW.md).

Use your organization's approved Python 3.12 installation (Python 3.11 also works). **Windows runs natively; WSL and Docker are not required.** From the repository directory in Command Prompt or PowerShell:

```powershell
.\run-windows.cmd
```

You can also double-click `run-windows.cmd`. It finds the Python launcher or `python` on PATH; set `AIRISK_PYTHON` to the full path of an approved `python.exe` if needed. It creates a separate `.venv-windows/` environment, so an existing Unix environment is preserved. No administrator access or PowerShell execution-policy change is required by the launcher.

On Linux or macOS:

```bash
bash run-local.sh
```

The launcher starts Waitress bound to localhost. On first use, it opens a private setup link to create an administrator; keep that link to yourself. Later visits use [http://127.0.0.1:5000](http://127.0.0.1:5000). Add a separate reviewer from **Team**. There are no default credentials. Source-launcher dependencies are installed on first use and when `requirements.txt` changes; subsequent starts work without downloading packages. The packaged executable includes its dependencies. Use `--no-browser`, `--port 5050`, or `--prepare-only` when needed.

Data is stored in `instance/airisk.db` and survives restarts. The local signing key is generated once in `instance/secret.key`. Both are ignored by Git. Stop the server with Ctrl+C.

To explore three clearly labeled fictional examples after creating your account:

```powershell
.\.venv-windows\Scripts\python.exe -m flask --app app seed-demo --owner your-email@example.com
```

On Linux/macOS, use `.venv/bin/python` instead. Existing accounts, assessments and seeded examples are reused from the same `instance/` directory when switching launchers; stop the old server before switching. Do not reseed a database that already contains demo records.

## Complete a real assessment

1. Add the vendor product, retention/training terms, processing regions, contract notes and next review date. Record dated evidence from your document store or test results.
2. Add an AI use case: purpose, affected people, accountable owner, model/version, data, retrieval, tool authority and EU scope/role.
3. Start an assessment. The application selects relevant scenarios from its 16-risk starter catalog. Add any organization-specific AI scenarios identified through consultation or testing.
4. Record inherent and residual likelihood/impact, the reasoning, controls actually operating, and linked evidence. Unknown values remain unassessed. Justify exclusions individually. Assign an owner and due date to each action.
5. Request independent review. A different reviewer verifies evidence, records legal applicability, and approves, approves with conditions, or rejects the use case. Missing evidence, unresolved scope, possible prohibited practices and critical residual risks block approval.
6. Export JSON, CSV, or the printable report using your browser's Save as PDF. Monitor review dates, evidence expiry, vendor changes and overdue actions. Conditional actions accept completion evidence and a separate verification. Original decisions remain preserved.

Changed system/vendor context requires a new assessment. Closing an action does not silently lower a risk rating or broaden an authorization. Decided assessments cannot be edited; retain them as the historical record.

## Use Claude to help draft and review

Open **Claude assistance** inside an assessment. Prepare a focused prompt for Claude.ai or your Claude Enterprise workspace, bring its JSON response back, and compare its suggestions with the current assessment. Owners explicitly accept selected draft reasoning and ratings; reviewers can request a critique without changing the assessment or decision.

This is a web handoff that works with your existing Claude access. Sharing happens when you copy or upload the prepared context into Claude. AIrisk validates imported suggestions, detects changed context, and records provenance. See [the Claude workflow guide](docs/CLAUDE_ASSISTANCE.md) for the steps and the future Enterprise connector/API integration path.

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

```powershell
.\.venv-windows\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-windows\Scripts\python.exe -m flask --app app db check
```

Use `.venv/bin/python` for these commands on Linux/macOS. The tests include a real Waitress process, restart/persistence, paths containing spaces and occupied-port handling. The same suite runs on native Windows.

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

For native Windows browser checks, see [operations](docs/OPERATIONS.md#native-windows-browser-validation). Browser screenshots and a generated PDF are written to ignored `test-results/`. GitHub Actions runs one Linux/Python 3.12 validation job on pull requests: workflow tests, migrations, dependency audit, the browser journey and a container startup check. Superseded runs are cancelled; documentation-only changes and merges do not repeat the pipeline. Native Windows validation runs locally to avoid adding a paid runner matrix. Python 3.11 was also validated for the initial release.

## Hosted operation

A non-root Gunicorn container, persistent volume and health endpoint are provided. Use [the operations guide](docs/OPERATIONS.md) to configure a strong signing secret, HTTPS, accounts and backups. The production configuration requires HTTPS for session cookies and disables browser-based first-account setup.

This release has local accounts, shared organizational visibility and SQLite storage. SSO/MFA, isolated tenants, evidence file storage, automated notifications, a complete obligation engine and externally tested high availability remain future work. Do not treat the presence of an approval badge as proof that the underlying AI is safe or legally compliant.
