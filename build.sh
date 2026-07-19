#!/usr/bin/env bash
set -o errexit
set -o pipefail

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
