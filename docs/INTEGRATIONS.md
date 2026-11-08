# Enterprise integrations

AIrisk 1.2 includes HaloITSM remediation tickets, OneTrust assessment handoff and Coupa requisition-line intake. Connections are disabled until their host environment is configured. Credentials are never collected by a browser form, stored in SQLite, included in exports, or written to logs. OAuth bearer tokens exist only in process memory.

The adapters have automated simulated-API and browser acceptance tests. No customer tenant was available during implementation. Validate the mappings and service account permissions in your tenant before enabling production use. Tenant-specific API licensing, templates, custom fields and permissions must be supplied by the administrator.

## Workflow

* **HaloITSM:** create one ticket from a saved remediation action; read back status, assignment, due date and the latest public note. Closure is advisory until independently verified in AIrisk. See [HaloITSM setup](HALOITSM.md).
* **Coupa:** open **Integrations**, import a requisition ID and its line ID, then choose **Complete AI context**. Supplier/product facts are imported; AI purpose, owner, data sensitivity and deployment context must be completed by a person. Imported supplier governance fields start unresolved. Each line has its own use case. The latest assessment's current review status, bounded summary and optional backlink can be explicitly published to dedicated line custom fields.
* **OneTrust:** open **Connected assessments & procurement** on an AIrisk assessment. Launch using a configured template/respondent or link an existing assessment UUID. Open the connection to sync status, deadline, result and risk counts. Preview and explicitly publish the AIrisk summary into a dedicated TEXTBOX question while the OneTrust assessment is not started or in progress. Respondents and approvers continue their normal OneTrust workflow.

External status never changes AIrisk ratings, legal review, decisions or remediation verification. Coupa purchase/approval endpoints and OneTrust assessment approval/submission endpoints are not called. Procurement status is not evidence that an AI use is approved. Summaries distinguish the recorded decision from the current monitoring status. Open remediation, expired evidence/reviews and changed context remain visible.

## Configuration

Set environment variables for the Windows account/service that runs AIrisk, then restart it. Supply secrets through your organization's host secret management. Do not commit credentials or paste them into chat. For scheduled polling, the task must inherit the same environment and use the same account and `--instance` directory as the desktop application.

`AIRISK_PUBLIC_URL` is optional: an HTTPS base URL at which recipients can open AIrisk. Leave it empty for a desktop-only instance; AIrisk will omit inaccessible backlinks. No automatic public hosting or network exposure is created.

Both new connectors require these variables, with `ONETRUST_` or `COUPA_` as the prefix:

| Suffix | Meaning |
| --- | --- |
| `API_URL` | OneTrust tenant origin, e.g. `https://tenant.onetrust.com`; Coupa API root, e.g. `https://tenant.coupahost.com/api` |
| `TOKEN_URL` | Tenant OAuth client-credentials token URL; OneTrust commonly ends in `/api/access/v1/oauth/token`, Coupa in `/oauth2/token`. Confirm in your tenant. |
| `CLIENT_ID`, `CLIENT_SECRET` | Dedicated integration client credentials |
| `SCOPE` | Explicit space-separated OAuth scopes approved for your client. Use OneTrust assessment access (`ASSESSMENT`, with read access as required by your tenant); Coupa requisition read/update and supplier read scopes for your tenant. No default broad scope is assumed. |
| `RECORD_URL_TEMPLATE` | HTTPS browser URL containing exactly one `{id}`. OneTrust substitutes the assessment UUID; Coupa substitutes the requisition ID. Copy the tenant's actual UI route. |

The settings page shows whether the variables are present and syntactically valid. **Configured** is not a successful live connection test. The first import/link/sync verifies authenticated access. Missing credentials, invalid configuration and API failures leave the local review intact.

### OneTrust mappings

| Variable | Purpose |
| --- | --- |
| `ONETRUST_TEMPLATE_ID` | Assessment template UUID, required for launching |
| `ONETRUST_RESPONDENT_ID` | Respondent UUID, required for launching |
| `ONETRUST_RESPONDENT_NAME` | Respondent display name, required for launching |
| `ONETRUST_ORG_GROUP_ID` | Optional destination organization group UUID |
| `ONETRUST_SECTION_ID` | Section UUID containing the dedicated AIrisk summary question |
| `ONETRUST_QUESTION_ID` | Question UUID of that TEXTBOX; required for summary publication |

Linking and progress sync do not require template/respondent or summary mappings. Reserve the question for AIrisk and give it enough space for approximately 4,000 characters. It must be present and editable in the exported assessment. The first write requires an empty answer; subsequent writes require the exact previous AIrisk text or the proposed text. Human edits are preserved: AIrisk refuses to overwrite them. Section/question IDs are pinned on first publication and cannot silently be retargeted by changing configuration.

Launch names carry `[AIrisk:<unique reference>]`. If the response is lost after OneTrust creates the assessment, AIrisk blocks another launch. Search OneTrust for the displayed reference and recover with its UUID. Only a matching reference is accepted for recovery. An administrator can reset a failed launch after confirming no such assessment exists. Do not reset merely because the first search returned no immediate result; allow OneTrust indexing to settle.

### Coupa mappings

Create three API-visible, writable custom fields **on requisition lines**, dedicated to AIrisk. Configure their API names, not display labels:

| Variable | Field |
| --- | --- |
| `COUPA_STATUS_FIELD` | Text review status: `pending`, `approved`, `conditional`, `rejected` or `review_required` |
| `COUPA_SUMMARY_FIELD` | Multiline text summary, allow at least 4,000 characters |
| `COUPA_URL_FIELD` | Text/URL backlink; allow empty when `AIRISK_PUBLIC_URL` is not set |

All mappings are needed for publishing; intake and polling work without them. Confirm your tenant supports updating these line custom fields with `PUT /api/requisitions/{id}` in each relevant requisition state. The request contains only the selected line ID and configured custom fields. AIrisk reads back all three fields before reporting confirmation. It never updates header approval status, prices, quantities or unrelated lines. Field mapping changes are blocked for previously published records.

Import uses the actual line ID, not `line-num`. Supplier resolution is from that line, including multi-supplier requisitions. Repeated imports return the existing record and do not create duplicate use cases. A use case owner must retain the imported vendor during initial registration. Later supplier/product changes in Coupa raise a persistent monitoring flag; local governance facts are not silently overwritten. Compare both versions, update the local context and reassess before relying on an earlier decision. The v1.2 flag remains visible to retain this provenance.

## Polling and recovery

After starting the upgraded application once so migrations finish, schedule a single command every five minutes:

```powershell
AIrisk.exe --instance "C:\Approved\AIriskData" integrations-sync --limit 50
```

For a source deployment:

```bash
python -m flask --app app integrations-sync --limit 50
```

The limit applies to up to 50 due OneTrust/Coupa records combined and up to 50 Halo tickets. Use a higher limit (maximum 200) if needed. Polling only reads remote records and updates local observations, including read-back confirmation of an earlier publication. It never launches, publishes, creates tickets, approves purchases, or installs a scheduled task. `halo-sync` remains available for Halo-only polling.

Commands return nonzero on failures. Inspect the relevant connection's last error and retry time, plus task output for missing configuration. Polling records are retried with exponential backoff and bounded `Retry-After`. Manual operations honor the same backoff. A three-minute committed lease coordinates OneTrust/Coupa browser and scheduled operations; Halo uses two minutes. No SQLite write lock is held during network access. Each HTTP request has a 15-second timeout, a 2 MiB response cap and no redirects. A 401 permits one token refresh; ambiguous writes are never automatically repeated.

OneTrust response submission may return 202. A summary is **pending** until a later GET returns the exact submitted text. Coupa confirmation likewise requires exact read-back of all configured fields. A timeout can leave a pending write; sync first. If still absent, an explicit publication retry writes the same reserved destination. Remote human edits block the retry. Polling recognizes changes to previously confirmed publications. Revisit the preview after local review changes; there is no unattended publication of new decisions.

## Tenant acceptance checklist

1. Use sandbox credentials and a test OneTrust template plus a Coupa requisition with two supplier lines.
2. Import one line; verify the correct supplier, unresolved vendor governance and required AI context. Repeat import and confirm the same record returns.
3. Launch or link OneTrust. Verify the template, respondent, reference and status sync. Publish a summary and confirm only the reserved question changes.
4. Publish the latest assessment to Coupa; verify the selected line's three fields and that the second line, pricing and purchase status are unchanged. Test draft and later requisition states supported by your configuration.
5. Change a remote status and poll with the scheduled account. Check timestamps and audit history. Confirm AIrisk review and action verification are unchanged.
6. Change an external summary manually; confirm AIrisk refuses to overwrite it. Test rate limits/permission removal and recovery of an interrupted launch using the reference.
7. Run the Halo tenant checks in [its guide](HALOITSM.md). Retain the results with your IT release approval.

## API contracts

Implemented against the provider documentation reviewed on 2026-11-08:

* OneTrust [Launch Assessment](https://developer.onetrust.com/onetrust/reference/createassessmentusingpost), [Get Assessment](https://developer.onetrust.com/onetrust/reference/exportassessmentusingget), [Submit Responses](https://developer.onetrust.com/onetrust/reference/submitresponsesusingpost) and [OAuth quick start](https://developer.onetrust.com/onetrust/reference/quick-start-guide).
* Coupa [Requisitions API](https://compass.coupa.com/en-us/products/product-documentation/integration-technical-documentation/the-coupa-core-api/resources/transactional-resources/requisitions-api-%28requisitions%29) and [examples](https://compass.coupa.com/en-us/products/product-documentation/integration-technical-documentation/the-coupa-core-api/resources/transactional-resources/requisitions-api-%28requisitions%29/requisitions-api-example-calls). Coupa is migrating documentation to its [current API documentation](https://docs.coupa.com/en/developer-documentation/the-coupa-core-api); confirm your tenant's custom-field exposure and OAuth scopes there.

Only bounded progress and the requested publication payload are stored. OneTrust questionnaire answers are inspected in memory to locate the reserved question; full exports, notes, attachments, private procurement comments, tax identifiers and credentials are not persisted. AIrisk remains a single-organization workspace where signed-in users can read records. Only the use case owner/assessment author or an administrator can publish; reviewers can sync progress. Existing session, CSRF, host and origin controls apply to every browser mutation.
