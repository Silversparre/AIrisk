# Operating AIrisk

This release supports one organization per deployment and a modest number of internal users. Use a separate deployment/database for unrelated organizations. Every active account can read the workspace's records; roles limit changes and approvals, not document visibility.

## Local pilot

Run `bash run-local.sh` with Python 3.11/3.12. The server binds to `127.0.0.1:5000`. Create the first administrator in the browser, then create a separate reviewer in Team. The browser setup route is available only in development, from a loopback address, before the first account exists. Do not expose development mode through a public reverse proxy.

The launcher creates `.venv/`, installs `requirements.txt`, applies migrations and runs Gunicorn. Dependencies require internet access on first installation. Normal operation has no external API dependency. Follow source/evidence links in the browser when reviewing them.

Optional manual startup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m flask --app app db upgrade
.venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 'app:create_app()'
```

## Production container

Provision HTTPS at your ingress/reverse proxy, retain the database volume, and keep the application reachable only through the intended internal route. The supplied Compose port binds to the host's loopback interface. Configure your HTTPS proxy to forward to `127.0.0.1:8080`. No proxy headers are trusted for authentication or setup access.

Generate the signing secret without printing it and keep it in your approved secret store or protected environment configuration:

```bash
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
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

The health endpoint `/health` returns 200 when the schema is accessible and 503 otherwise. It exposes no assessment information. Gunicorn logs are written to stdout/stderr. Set your ingress request limits and authentication monitoring for your environment; the application adds per-account login throttling, not a comprehensive network abuse-control service.

## Accounts

Admin accounts can create and disable members/reviewers. The creator shares an initial password through an approved internal channel; the recipient changes it in Account. There is no self-registration or email delivery. Passwords must be at least 12 characters. There is no SSO/MFA integration in this release.

A server administrator can reset a password interactively:

```bash
.venv/bin/python -m flask --app app reset-password --email person@example.com
# Container equivalent:
docker compose exec app python -m flask --app app reset-password --email person@example.com
```

Resetting/changing a password or disabling an account revokes existing sessions. Administrators should maintain at least two functioning accounts with review authority; the application prevents disabling your own account.

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

For containers, run the same SQLite backup procedure inside the container against `/app/instance/airisk.db`, then copy the completed backup to protected off-host storage. Copying a live database file directly is not the supported backup procedure.

To restore: stop the application, preserve the current database separately, restore a verified backup to the configured database path, make it writable by the service account, apply migrations, and start the server. Verify `/health`, sign in, and check a known assessment and its evidence. The automated startup test upgrades a populated schema, creates a SQLite backup, restores it into a separate application instance, and checks integrity and retained data. Operators still need to exercise their actual volume and off-host recovery process.

## Updates and source maintenance

Back up before deploying a new version. Apply migrations using `python -m flask --app app db upgrade`; run `db check` in development/CI to detect schema drift. Do not downgrade a populated database as a routine rollback; restore the matching backup with the previous application version instead.

The old prototype used `instance/risk_assessment.db`; the new application uses `instance/airisk.db`. The committed legacy database was empty, and the old application did not save completed wizard records. No automatic conversion of unknown external legacy databases or browser cookies is attempted. Preserve any private legacy data and assess migration separately.

Review source changes in `airisk/catalog.py` before its declared review date, update the version/date and source metadata, review affected scenarios and the method page, and rerun workflow tests. Catalog expiry blocks new approvals. Framework changes do not rewrite old assessments. The EU classification screen is intentionally not a complete rules engine: reviewers must use current primary sources and document any specialist advice.

## Release checks

Run the workflow/startup tests, migration check, dependency audit, Chromium journey and container health test before merging. CI produces browser screenshots and a PDF report artifact. The browser fixture creates accounts and fictional vendor data, so run it only against a fresh disposable database. The test suite does not certify third-party AI behavior or replace actual security testing of a deployed environment.
