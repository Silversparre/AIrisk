# HaloITSM remediation integration

AIrisk creates a HaloITSM ticket from a saved remediation action and polls progress back into the assessment. The connection is disabled until configured. There are no demo credentials, automatic ticket creation, or incoming webhook endpoints.

The connector uses OAuth client credentials, `POST /Tickets` with a one-element JSON array, `GET /Tickets/{id}`, and `GET /Actions`. Fields and response shapes were checked against the schema published with [Halo's Swagger documentation](https://www.usehalo.com/swagger) on 8 November 2026. Automated tests simulate Halo; a real tenant acceptance test remains necessary because routing, permissions, statuses, and mandatory fields vary by installation.

## Configure the host

Register a dedicated API application in Halo using client credentials. Assign an integration agent with access to the remediation ticket type and intended team. Start with `read:tickets edit:tickets` and restrict the agent's record access appropriately. Copy the API and authorization addresses from Halo's integration settings; authentication may use a different hostname. See [Halo's authentication overview](https://haloitsm.com/apidoc/).

Inject these environment variables through the host's normal configuration and secret management. Restart AIrisk after changes and give the scheduled sync process the same settings. AIrisk does not automatically read `.env` files. Do not commit credentials or supply them in command-line arguments.

| Variable | Required | Meaning |
| --- | --- | --- |
| `HALO_API_URL` | Yes | HTTPS API base, usually `https://your-halo-host/api`. No query or fragment. |
| `HALO_TOKEN_URL` | Yes | Full HTTPS OAuth endpoint including `/token`. An instance-specific query such as `?tenant=...` is preserved when needed. |
| `HALO_CLIENT_ID` | Yes | OAuth client identifier. |
| `HALO_CLIENT_SECRET` | Yes | Secret supplied through the host's secret management. |
| `HALO_TICKET_URL_TEMPLATE` | Yes | Agent ticket URL containing exactly one `{id}`, e.g. `https://your-halo-host/ticket?id={id}`. Verify the route in your installation. |
| `HALO_TICKET_TYPE_ID` | Yes | Numeric remediation ticket type ID. |
| `HALO_TEAM`, `HALO_AGENT_ID` | No | Default team name and Halo assignee ID. |
| `HALO_CUSTOMER_ID` | If required by Halo | Customer ID sent as `client_id`; distinct from the OAuth client identifier. |
| `HALO_SITE_ID`, `HALO_USER_ID` | If required by Halo | Site and requesting user IDs. |
| `HALO_PRIORITY_ID` | No | Default Halo priority ID. |
| `HALO_COMPLETED_STATUS_IDS` | Recommended | Comma-separated IDs meaning completed remediation. Exclude cancelled, rejected and duplicate statuses. Without this mapping, statuses display but are never classified as completed. |
| `HALO_SCOPE` | No | Defaults to `read:tickets edit:tickets`. Match your application's approved scopes. |
| `AIRISK_PUBLIC_URL` | No | HTTPS workspace address for assessment backlinks. Leave unset for a localhost-only installation that Halo agents cannot access. |

URLs require valid HTTPS certificates. Redirects are not followed and certificate verification cannot be disabled. Provision private CAs through the normal host trust configuration when needed. Destinations are administrator-controlled, so self-hosted Halo endpoints are supported.

An administrator can open **Integrations → HaloITSM** to see missing configuration, routing settings, recent links and errors. “Configuration is present” means local validation passed, not that a connection was tested.

## Create and follow tickets

Save the action with an active owner and due date. Choose **Create Halo ticket** on an open action in a draft, in-review, or conditional assessment. The action owner, assessment author, system owner, or administrator can create it; reviewers can refresh existing links. An unrelated member cannot send it to Halo.

The ticket includes the action, system and risk names, assessment/action IDs, owner's name/email, due date, configured routing, and a unique correlation reference. It includes an assessment link when configured. It excludes the full assessment, ratings, contracts and evidence register. The due date becomes Halo's `targetdate` at `23:59:59Z`; check its display in your Halo timezone. The Halo assignee uses the configured default; the AIrisk owner is described in the ticket and is not automatically matched to a Halo agent.

Creation attempts an immediate progress sync. **Sync Halo progress** then refreshes status, team/assignee, target date, remote update date, and a recent public note. Private notes and attachments are not imported. The existing shared workspace visibility applies to imported progress.

Progress stays separate from the original assessment. A completed Halo status does not change ratings, mark the action done, or approve AI use. On conditional decisions, the responsible person reviews the ticket and uses **Submit action completion**; a different reviewer verifies it. The ticket URL is prefilled as the evidence link. Draft assessments continue to use the existing assessment review workflow.

If Halo returns to an incomplete status after reporting completion, AIrisk flags **Halo remediation reopened**. Earlier verification and decisions remain preserved for renewed review. The flag clears when Halo again reports a mapped completion status.

One risk/action record has one ticket. Later edits to the action, owner or due date are flagged for reconciliation without overwriting Halo. Review and update the ticket manually. Reassessment creates new action records that can have separate tickets.

## Schedule polling

The command makes one bounded pass through due linked tickets and exits. It never creates tickets. Successful links become due after five minutes. Schedule it every five minutes through your organization's scheduler; AIrisk does not install a task or service automatically.

Linux/macOS source deployment, from the repository directory:

```bash
.venv/bin/python -m flask --app app halo-sync --limit 50
```

Native Windows source deployment:

```powershell
.\.venv-windows\Scripts\python.exe -m flask --app app halo-sync --limit 50
```

In Windows Task Scheduler, select that interpreter as the program, `-m flask --app app halo-sync --limit 50` as arguments, and the repository directory as **Start in**. Run under the data owner's account with the configured credentials. Match `AIRISK_INSTANCE`, `DATABASE_URL` when used, and Halo settings to the serving app.

A newly rebuilt Windows package also supports:

```powershell
.\AIrisk\AIrisk.exe halo-sync --limit 50
# Select the same data directory as the running app when overriding the default:
.\AIrisk\AIrisk.exe --instance 'C:\Users\YourName\AIrisk Data' halo-sync --limit 50
```

The package's dedicated `halo-sync` command can run alongside the local server. It performs no migrations and coordinates operations through ticket leases. Start the upgraded application once to apply migrations before scheduling. Older packaged executables need rebuilding. Do not use `admin halo-sync` for concurrent polling: general administrator maintenance still acquires the exclusive server lock.

The batch limit is 1–200. Increase it or schedule more frequently if batches regularly fill; monitor the output and nonzero exit status. The host must be running. Outbound polling avoids exposing a workstation to incoming internet requests.

## Failures and recovery

- Repeated creation clicks reuse the link. Unique action constraints and committed operation claims prevent simultaneous POSTs.
- Explicit request/authentication/permission rejections can be retried after correction and the displayed retry time. Failed syncs preserve the last observed progress and timestamp. `Retry-After` and exponential backoff defer requests.
- A timeout, server failure, malformed creation response, or interrupted process can leave creation uncertain. AIrisk never automatically repeats that POST. Search Halo for the displayed `[AIrisk:...]` reference, enter its ticket ID, and choose **Recover matching ticket**. Recovery checks the reference before linking.
- A live operation holds a two-minute lease. After an interrupted process, wait for the lease to expire before recovery. Expiry never authorizes automatic recreation.
- Only after checking that no ticket exists can an administrator confirm absence and reset creation. An incorrect absence confirmation can create a duplicate.
- Changing the API URL does not redirect existing links to another tenant. Restore the original configuration to sync them. This version supports one configured Halo connection per workspace.

Audit history records creation, recovery, failures and changed progress. Unchanged polls do not duplicate history. JSON and printable reports include links, progress, last sync and reconciliation flags; the risk CSV format is unchanged. Tokens and secrets are not persisted in ticket/audit records, and raw remote errors are not displayed.

## Tenant acceptance test and boundaries

Run the backend suite and the standalone browser fixture before changing the connector:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/halo_browser.py
```

The browser fixture requires the repository's locked Playwright dependencies and an installed Chromium browser. On Windows, use `.venv-windows\Scripts\python.exe`; `--node` and `--browser` can select installed Node and Edge executables. The fixture starts a disposable server, uses simulated Halo responses, exercises creation/progress/verification/reopening, and writes desktop/mobile screenshots under ignored `test-results/`. CI runs it after the general browser journey. `--baseline` runs that existing general journey against a separate disposable server.

In a test tenant, create one synthetic action and check routing, the accountable owner, target date and backlink. Change assignment/status and add a public note, then sync. Exercise completed → reopened → completed and verify that only the normal independent verification resolves the AIrisk action. Also test required fields and temporarily disabled credentials.

Ticket types requiring mandatory custom fields beyond the supported defaults need an explicit mapping extension before use. This version does not synchronize attachments, private notes, deletion, every intermediate status transition, or later AIrisk edits back to Halo. Polling records observed snapshots; Halo retains the detailed ticket history.
