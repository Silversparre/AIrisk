# Review and product roadmap

Reviewed 7 November 2026. Baseline: commit `74b6823ed6a702dffa2c6b572d5d5206bfb5e39f` in [Silversparre/AIrisk](https://github.com/Silversparre/AIrisk).

## Product decision

Build an AI adoption decision workspace for organizations evaluating vendor products and internal use cases. Its core question is: **Can we use this AI for this purpose, with these data and controls, and who accepts the remaining risk?**

The original generic risk calculator could not answer that question. It collected adversary capability, intent and targeting, produced a score and lost the organizational context. A useful product needs connected records for the use case, supplier, affected people, evidence, treatment, decision and later changes. Keep the small Flask/Jinja stack; replacing it with a larger frontend framework would not solve these missing concepts.

## Baseline findings

These were checked against source and executable behavior, not inferred from the generated presentations. The [raw baseline results](../review/baseline_results_2026-11-07.json) and [reproduction script](../review/reproduce_review.py) preserve the evidence. Run that script against the baseline checkout, not this rebuilt application.

| Priority | Finding and user impact | Evidence | Resolution in this release |
| --- | --- | --- | --- |
| P0 | An empty or skipped assessment appeared “Very Low.” This created a positive result without an assessment. | Empty calculation/result and direct step-10 submission; `empty_result`, `skip_to_end` | Unknown remains unassessed; completeness and evidence checks gate decisions |
| P0 | No completed assessment was persisted, despite database models and documentation suggesting otherwise. | Complete ten-step workflow left both database tables at zero rows | Persistent systems, vendors, assessments, risks, evidence, decisions and history |
| P0 | The product modeled generic adversarial risks rather than AI adoption. | Old wizard questions and `risk_logic.py`; no AI RMF/OWASP/EU domain model | Contextual AI scenarios, source mappings, role/scope screening and human applicability review |
| P1 | Arbitrary fields and invalid scores were accepted; negative scores became low risk. | `arbitrary_fields_and_invalid_score`, `negative_scores`; invalid steps 0/11/999 returned 200 | Explicit input validation, bounded ratings, route checks and database constraints |
| P1 | Assessment content lived in a readable signed cookie, with a known fallback signing key. | Runtime cookie decoded without the signing key; fallback active | Assessment content in the database; identity-only session, generated local key, mandatory production secret |
| P1 | No identity, ownership, independent review or decision accountability. | Route/model inspection | Accounts, roles, controlled edits, evidence verification and independent decision workflow |
| P1 | Back navigation did not prefill saved answers and the final step did not show them. | `back_navigation_prefills_value`, `review_step_displays_answers` | Saved editable risk records and complete decision reports |
| P1 | Documentation asserted remediation and architecture that the code did not implement. | Source/runtime compared with root reports and architecture text | Current docs rewritten; original generated reports clearly archived |
| P1 | Delivery confidence was limited to two calculation tests and a development-server container. | Original tests and Dockerfile | Workflow/startup tests, real browser test, Gunicorn, migrations and CI container check |

CSRF protection did work in the original runtime: missing-token submissions returned 400. The review does not claim that every pre-existing protection was broken. No external penetration test or production customer data review was performed.

## Framework design

The three inputs serve different purposes; combining them into one “compliance percentage” would be misleading.

| Input | Product responsibility | Concrete artifact |
| --- | --- | --- |
| NIST AI RMF | Organize governance, context, evaluation and risk treatment | Owner, system inventory, affected people/purpose, evaluation evidence, residual-risk decision and review cadence |
| OWASP | Identify technical failure and abuse scenarios for applicable architectures | Injection, sensitive disclosure, supply chain, output handling, excessive agency, retrieval, misinformation and consumption scenarios with evidence prompts |
| EU AI Act | Establish whether duties apply to this organization, role and use case | Conservative scope routing plus a dated human review, recorded rationale and decision restrictions |

The catalog pins [NIST AI RMF 1.0](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf), [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf), [OWASP LLM 2025](https://genai.owasp.org/llm-top-10/) and [OWASP Agentic 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/). Original scenarios cite relevant source sections; they do not claim every framework outcome has been implemented.

Legal content was checked against the [27 July 2026 consolidation](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02024R1689-20260727) and the [Commission implementation timetable](https://ai-act-service-desk.ec.europa.eu/en/ai-act/eu-ai-act-implementation-timeline). The timetable distinguishes December 2027 Annex III and August 2028 Annex I high-risk application dates, with separate transparency/transitional provisions. Dates and role-specific exceptions must remain versioned; a frozen 2024 summary is insufficient. The current product leaves the final applicability determination to a reviewer and does not infer exemptions from the presence of human oversight.

Source metadata and scenario text are copied into each assessment. The catalog has a review deadline. An expired catalog blocks new approvals and displays a monitoring signal; an editor must review current sources and publish a new version. This is a deliberate content-maintenance obligation for the product owner.

## Delivered product

The release contains a complete first adoption cycle:

- System and vendor inventory, with accountable owner, model/version, data sensitivity, retrieval, tool authority, audience and lifecycle.
- Sixteen original AI risk templates selected by use context, plus organization-defined scenarios. Predictive hiring receives a different set from an agent with retrieval/tools.
- Inherent/residual ratings, uncertainty preserved as unassessed, control rationale, justified exclusions, assigned actions and due dates.
- Dated evidence notes/links, independent verification and reusable vendor evidence. Evidence is not verified merely because an author attached a link.
- Draft, review, approve/conditional/reject decisions. Suspected prohibited use, missing legal scope, missing/expired evidence and critical residual risk prevent approval.
- Immutable decisions, context-change detection, review/evidence/action attention signals, separately verified completion of conditional actions, audit history and exports.
- Claude.ai/Claude Enterprise web handoff for scoped draft assistance and independent-review critique, with strict response validation, provenance and human acceptance.
- Local setup without default credentials, role checks, session revocation, CSRF, input validation, concurrent-edit checks, migrations, production configuration and automated checks.
- Native Windows startup with an approved Python installation and Waitress, a reusable Windows environment, preserved local data and no WSL or Docker requirement. Linux/macOS use the same local server; the optional Linux container remains supported.

The rating bands are transparent local policy: likelihood × impact, Low 1–5, Moderate 6–11, High 12–19, Critical 20–25. They prioritize discussion; they are neither a measured probability nor an EU legal classification. Custom scoring policy and calibration across teams remain future work.

## Validation and limits

The executable acceptance journey uses separate author and reviewer accounts in Chromium against Gunicorn and a fresh SQLite database. It registers a fictional supplier, assesses an internal policy assistant, adds a custom risk, verifies evidence, makes a decision and exports JSON/CSV/PDF. It also checks predictive hiring routing, agentic scenarios and desktop/mobile layouts. This is realistic application workflow testing with synthetic data, not a test of any real supplier's model or a substitute for user interviews.

The backend tests exercise the complete decision flow, data persistence across application recreation, populated-schema migration and backup restoration, permissions, CSRF, stale edits, invalid scores, self-approval prevention, evidence expiry, vendor changes, action verification, custom risks, password/session revocation, production configuration, unsafe URLs and CSV formula handling. Dependency audit findings in Flask and Werkzeug were addressed before release. CI additionally validates the container in an environment with Docker available.

Native Windows checks exercise the same backend suite and browser journeys with Windows Python, Node and Edge. Local-server checks cover restart/persistence, paths containing spaces and exclusive port binding. Windows-specific test cleanup closes database handles and terminates the test server's full process tree. These checks run locally; the CI workflow retains one Linux job to limit Actions usage.

## Next increments, in order

| Priority | Increment | Acceptance criteria |
| --- | --- | --- |
| P1 | Claude Enterprise connector or API integration | Web handoff is available now. Add an organization-approved authenticated connector or separately configured API access, with per-user context access, bounded suggestions, explicit human acceptance and a live Enterprise acceptance test. See [Claude assistance](CLAUDE_ASSISTANCE.md). |
| P1 | Pilot with actual procurement, security, privacy/legal and business owners | Three real use cases completed: internal knowledge assistant, customer agent and recruitment AI. Reviewers can identify the evidence behind each decision. Capture task completion time, abandoned fields and repeated clarification requests. |
| P1 | Structured legal obligation register | Separate obligation records with article/annex, role, jurisdiction, effective date, applicability rationale, owner and evidence. Expert-reviewed fixtures for Article 5, Article 6 exceptions, Article 25 role changes, Article 26 duties, restricted Article 27 FRIA scope and Article 50 distinctions. Legal unknowns never become “not applicable” automatically. |
| P1 | Enterprise access and retention | Organization SSO/MFA, account lifecycle integration, explicit access groups if records need restricted visibility, retention/deletion/export controls and audit export. Cross-group access-denial tests must pass before use with segregated teams. |
| P1 | Operational readiness for broader use | Validated PostgreSQL migrations, concurrency/load targets, actual ingress configuration, monitored backups and a documented recovery exercise on the deployment platform. No multi-instance claim until this passes. |
| P2 | Evidence and evaluation packs | Guided tests for permission-aware retrieval, direct/indirect injection, discriminatory outcomes, model/version changes, agent authorization and rollback. Every test records environment, sample, expected behavior, results, limitations and reviewer. |
| P2 | Monitoring and incident workflow | Material-change events, suspend-use decision, incident intake, reassessment linkage and opt-in review reminders. An incident can be traced to affected systems, owners, actions and a revised decision. |
| P2 | Reassessment and vendor collaboration | Controlled carry-forward of previous answers with mandatory reconfirmation, comparison of revisions, supplier questionnaire/export and evidence provenance. Changed source/context must invalidate affected answers rather than silently reuse them. |
| P3 | Analytics and interoperability | Measure evidence freshness, unresolved high-impact use, overdue actions and decision lead time; export a stable schema and integrate with an organization's existing register. Avoid a single “compliance score.” |

Do not add multi-tenant SaaS, an AI-generated auto-approval assistant or broad “all risks” modules before the core adoption workflow succeeds with actual organizations. The strongest next feedback is whether a reviewer can make and later defend a concrete decision using the recorded evidence.
