#!/usr/bin/env bash
#
# A1 360 in a container: bring the database up to date, then serve.
#
# Migrations run here rather than during the build because the private
# network this reaches Postgres over only exists at runtime — a build-time
# migrate cannot resolve postgres.railway.internal at all, and would either
# hang or quietly run against nothing.
#
# Both commands are idempotent, so a restart is safe and a redeploy that
# changes no models does no work. They are NOT safe to run concurrently:
# with more than one replica two containers would migrate at once. Keep the
# web service at one instance, or move these two lines into Railway's
# pre-deploy command, which runs once per release instead of once per boot.
#
set -euo pipefail

# Static files, here rather than trusting the build to have done it.
#
# Whether the builder runs collectstatic, and whether it runs with DEBUG on
# or off, varies by platform and is invisible until it bites: with DEBUG off
# the app serves static files from a hashed manifest that only collectstatic
# writes, and without that manifest every single page raises rather than
# falling back. Running it here costs a few seconds a boot and means the
# manifest always matches the code being served.
#
# It opens no database, so it runs before the database is even checked.
echo "--> collecting static files"
python manage.py collectstatic --noinput

# Wait for the database to be reachable before doing anything with it.
#
# A private network attaches a moment after the container starts, and a
# managed database can still be coming up when its first client arrives.
# Neither is an error worth crashing over — but a host that never resolves
# is, so this gives up rather than retrying forever, and says which it was.
echo "--> waiting for the database"
for attempt in $(seq 1 20); do
  if python -c "
import django, os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'inventoryproject.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>/tmp/dbwait.log; then
    echo "    reachable after ${attempt} attempt(s)"
    break
  fi
  if [ "$attempt" -eq 20 ]; then
    echo "    the database could not be reached after 20 attempts:"
    tail -3 /tmp/dbwait.log
    echo "    If the host name will not resolve, the private network is not"
    echo "    attached to this service. Use the public proxy host instead;"
    echo "    see docs/deploy-railway.md."
    exit 1
  fi
  sleep 3
done

echo "--> migrating"
python manage.py migrate --noinput

# The permission list and the pre-built roles, from the frozen registry.
# Existing roles are left exactly as they are unless --reset-system-roles is
# passed, so an Admin's changes in Settings survive every deploy.
echo "--> seeding permissions"
python manage.py seed_permissions

echo "--> serving on ${PORT:-8000}"
exec gunicorn inventoryproject.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout 60 \
  --access-logfile - \
  --error-logfile -
