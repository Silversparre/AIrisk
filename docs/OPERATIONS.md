# Operating AIrisk

This release supports one organization per deployment and a modest number of internal users. Use a separate deployment/database for unrelated organizations. Every active account can read the workspace's records; roles limit changes and approvals, not document visibility.

## Local pilot

On Windows, run `.\run-windows.cmd` in Command Prompt or PowerShell using an approved Python 3.12 installation (3.11 is also supported). WSL, Docker, Bash, Node and administrator privileges are not needed to run the application. On Linux/macOS use `bash run-local.sh`. Both launchers bind Waitress to `127.0.0.1:5000` and open the browser. Use `--no-browser` to suppress this or `--port 5050` to choose another port. Keep the console open and stop with Ctrl+C.

Create the first administrator using the private setup link opened/printed by the launcher, then create a separate reviewer in Team. The browser setup route requires that random link, development mode, a loopback address and an empty workspace. Manual WSGI startup does not generate the link; use the administrator CLI to create the first account. Do not expose development mode through a public reverse proxy. The [native Windows package](WINDOWS_IT_REVIEW.md) includes its interpreter and needs no pip/Python installation at runtime.

Windows uses `.venv-windows/`; Linux/macOS use `.venv/`. The launcher installs `requirements.txt` on first use and when that file changes, checks the installed packages, and applies migrations before starting the server. Normal restarts do not contact a package index. First installation/updates need access to the organization's approved Python package source; pip honors its normal index, proxy and offline-wheel configuration. No PowerShell execution-policy bypass is used.

The Windows launcher reuses its existing environment, otherwise tries `py -3.12`, `py -3.11` and `python`. For an approved Python installation outside PATH, set the path for the current terminal:

```powershell
$env:AIRISK_PYTHON = 'C:\Path To Approved Python\python.exe'
.\run-windows.cmd
```

`--prepare-only` installs/checks dependencies and applies migrations without starting a server. Application data stays in `instance/airisk.db` and the signing key in `instance/secret.key`. An existing database and accounts work with either platform's launcher. Stop the previous server before switching; do not run Windows and Unix servers against the same SQLite file simultaneously. Back up before updating the application. Do not delete `instance/` when replacing a virtual environment.

Optional manual native Windows startup, after preparing the environment:

```powershell
.\.venv-windows\Scripts\python.exe -m flask --app app db upgrade
.\.venv-windows\Scripts\waitress-serve.exe --host 127.0.0.1 --port 5000 --threads 4 --max-request-body-size 131072 --call app:create_app
```

For Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m flask --app app db upgrade
.venv/bin/waitress-serve --host 127.0.0.1 --port 5000 --threads 4 --max-request-body-size 131072 --call app:create_app
```

The local launcher rejects `APP_ENV=production` because production cookies require HTTPS. A hosted Windows deployment can use the same native environment with the manual commands above, `APP_ENV=production`, a stable protected `SECRET_KEY`, and an internal HTTPS reverse proxy. Create the first admin using `python -m flask --app app create-user --role admin`. Have the organization's service manager run Waitress under a dedicated identity with access to the database and secret; the launcher does not install a Windows service or configure IIS. The Linux container below is optional and is not a Windows prerequisite.

## Production container

Provision HTTPS at your ingress/reverse proxy, retain the database volume, and keep the application reachable only through the intended internal route. The supplied Compose port binds to the host's loopback interface. Configure your HTTPS proxy to forward to `127.0.0.1:8080`, preserving the public Host header. Set `AIRISK_TRUSTED_HOSTS` to the exact approved hostname(s), comma separated; include `127.0.0.1` for local health probes. Do not configure wildcard domains. Origin validation uses HTTPS in production. No proxy headers are trusted for authentication or setup access.

Generate the signing secret without printing it and keep it in your approved secret store or protected environment configuration:

```bash
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export AIRISK_TRUSTED_HOSTS="airisk.your-company.example,127.0.0.1"
docker compose build
docker compose run --rm app python -m flask --app app create-user --role admin
docker compose up -d
```

The account command prompts for email, name and password. The container applies migrations before running the command. Once signed in through HTTPS, create a reviewer from Team. `/setup` is disabled in production. Secure session cookies deliberately do not support a plain-HTTP production login; use the local launcher for a plain-HTTP local pilot.

Persist the same secret across restarts. Changing it invalidates all sessions. `APP_ENV=production` requires `SECRET_KEY` of at least 32 characters. The image runs as UID 10001 and uses `/app/instance` for its SQLite database. The named volume must remain writable by that user. Start only one application instance; multi-instance/rolling migration behavior is not supported in this release.

| Variable | Default / behavior |
| --- | --- |
| `APP_ENV` | `development`; use `production` for hosted operation |
| `SECRET_KEY` | Required in production; otherwise generated once under the instance directory |
| `AIRISK_INSTANCE` | Absolute path to the instance directory; defaults to the repository's `instance/` |
| `DATABASE_URL` | `sqlite:///airisk.db`; other database backends are not yet validated |
| `AIRISK_TRUSTED_HOSTS` | Loopback hosts by default; exact comma-separated hostnames for an HTTPS deployment |
| `AIRISK_SETUP_TOKEN` | Local launcher generates a private token; automated disposable tests may supply one of at least 32 characters |

The health endpoint `/health` returns 200 when the schema is accessible and 503 otherwise. It exposes no assessment information. Gunicorn logs are written to stdout/stderr. Set your ingress request limits and authentication monitoring for your environment. Atomic login budgets allow 10 attempts/account, 50/source and 200/workspace per 15 minutes; source/workspace budgets count successful logins too. A reverse proxy shares one source bucket because forwarded addresses are untrusted. Review these limits before a larger rollout; they are not a comprehensive network abuse-control service.

## HaloITSM integration

The optional connector creates remediation tickets on request and polls progress using `flask --app app halo-sync`. Configure the connection and schedule polling according to [HaloITSM setup and operation](HALOITSM.md). It needs outbound HTTPS to the configured API and OAuth endpoints. No inbound webhook or scheduler is installed automatically. Credentials belong in host configuration; the sync process must use the same database and instance as the application.

## Accounts

Admin accounts can create and disable members/reviewers. The creator shares an initial password through an approved internal channel; the recipient changes it in Account. There is no self-registration or email delivery. Passwords must be at least 12 characters. There is no SSO/MFA integration in this release.

A server administrator can reset a password interactively:

```powershell
.\.venv-windows\Scripts\python.exe -m flask --app app reset-password --email person@example.com
```

Linux/macOS or container equivalent:

```bash
.venv/bin/python -m flask --app app reset-password --email person@example.com
# Container equivalent:
docker compose exec app python -m flask --app app reset-password --email person@example.com
```

Logout revokes the current session server-side. Resetting/changing a password or disabling an account revokes existing sessions. Sessions have an eight-hour hard expiry. The security migration requires existing users to sign in again; it preserves their accounts and records. Administrators should maintain at least two functioning accounts with review authority; the application prevents disabling your own account.

## Backup and recovery

Back up the entire database using SQLite's online backup API, and store encrypted copies outside the application host/volume. Also retain the production signing secret in your secret manager. JSON/CSV assessment exports are useful decision records but are not complete database backups.

For a local installation, this command creates an exclusive, dated backup and refuses to overwrite an existing file:

```bash
python3 - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
source = Path('instance/airisk.db').resolve()
folder = Path('backups')
folder.mkdir(exist_ok=True)
backup = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.db')
with backup.open('xb'):
    pass
backup.chmod(0o600)
with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as src, sqlite3.connect(backup) as dst:
    src.backup(dst)
    assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print(backup)
PY
```

On Windows, an offline backup is also straightforward: stop AIrisk with Ctrl+C and confirm its server process has exited, then copy the closed database. Keep the backup and signing key in storage restricted to the appropriate Windows accounts:

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
$backup = Join-Path backups ((Get-Date -Format 'yyyyMMddTHHmmssfff') + '.db')
Copy-Item instance\airisk.db $backup -ErrorAction Stop
```

Only use this file-copy procedure while the application is stopped. For a live database, use the SQLite online backup API shown above (the Python code also runs on Windows).

For containers, run the same SQLite backup procedure inside the container against `/app/instance/airisk.db`, then copy the completed backup to protected off-host storage. Copying a live database file directly is not the supported backup procedure.

To restore: stop the application, preserve the current database separately, restore a verified backup to the configured database path, make it writable by the service account, apply migrations, and start the server. Verify `/health`, sign in, and check a known assessment and its evidence. The automated startup test upgrades a populated schema, creates a SQLite backup, restores it into a separate application instance, and checks integrity and retained data. Operators still need to exercise their actual volume and off-host recovery process.

## Updates and source maintenance

Back up before deploying a new version. Apply migrations using `python -m flask --app app db upgrade`; run `db check` in development/CI to detect schema drift. Do not downgrade a populated database as a routine rollback; restore the matching backup with the previous application version instead.

The old prototype used `instance/risk_assessment.db`; the new application uses `instance/airisk.db`. The committed legacy database was empty, and the old application did not save completed wizard records. No automatic conversion of unknown external legacy databases or browser cookies is attempted. Preserve any private legacy data and assess migration separately.

Review source changes in `airisk/catalog.py` before its declared review date, update the version/date and source metadata, review affected scenarios and the method page, and rerun workflow tests. Catalog expiry blocks new approvals. Framework changes do not rewrite old assessments. The EU classification screen is intentionally not a complete rules engine: reviewers must use current primary sources and document any specialist advice.

## Release checks

Run the workflow/startup tests, migration check, dependency audit, Chromium journey and container health test before merging. CI produces browser screenshots and a PDF report artifact. The browser fixture creates accounts and fictional vendor data, so run it only against a fresh disposable database. The test suite does not certify third-party AI behavior or replace actual security testing of a deployed environment.

### Native Windows browser validation

Use an approved native Node installation for development tests only. In one PowerShell window, start a disposable instance:

```powershell
$env:AIRISK_INSTANCE = Join-Path $env:TEMP ('airisk-browser-' + [guid]::NewGuid())
$env:AIRISK_SETUP_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
# Copy this disposable token to the test terminal; do not reuse a real setup link.
$env:AIRISK_SETUP_TOKEN
.\run-windows.cmd --port 5057 --no-browser
```

In a second PowerShell window in the repository:

```powershell
npm ci
npx playwright install chromium
$env:AIRISK_SETUP_TOKEN = 'PASTE_THE_DISPOSABLE_SERVER_TOKEN_HERE'
node tests/browser.cjs
```

Stop the disposable server after the test. Use a fresh `AIRISK_INSTANCE` for each run. For a different port, set `AIRISK_TEST_URL` in the test terminal. `AIRISK_BROWSER` optionally selects an already installed Chromium/Edge executable. The browser run covers account setup, author/reviewer workflows, Claude handoffs with synthetic responses, exports and mobile layout on the native server.

For the packaged executable, `python tests/package_windows.py --exe PATH_TO_AIrisk.exe --node PATH_TO_node.exe` starts a disposable instance itself, removes Python from the app's PATH, runs the browser journey, checks hostile Host/setup access, duplicate startup, restart persistence, administrator maintenance and live backup. Set `AIRISK_BROWSER` to an installed Edge/Chromium executable if needed. Package hashes, signing and the IT acceptance boundaries are described in [Windows IT review](WINDOWS_IT_REVIEW.md).

## OneTrust and Coupa integrations

[Enterprise integration setup](INTEGRATIONS.md) covers Coupa requisition-line intake, OneTrust assessment launch/link, explicit summary publication, read-only polling and recovery. The combined `integrations-sync --limit 50` command includes HaloITSM and can run alongside the desktop server after migrations have completed. Use the same account, credentials and data directory. No scheduled task is installed automatically.
