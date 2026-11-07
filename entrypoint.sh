#!/bin/sh
set -eu
python -m flask --app app db upgrade
exec "$@"
