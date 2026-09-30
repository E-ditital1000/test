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
