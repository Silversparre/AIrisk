#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
.venv/bin/python -m flask --app app db upgrade
printf '%s\n' 'AIrisk is available at http://127.0.0.1:5000. Create your account in the browser.'
exec .venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 'app:create_app()'
