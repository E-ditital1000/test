#!/usr/bin/env bash
#
# Update A1 360 on the VPS.
#
#   sudo -u a1360 /srv/a1360/deploy/deploy.sh
#
# Deliberately ordered so the site is never serving a half-updated release:
# fetch, install, migrate, collect, THEN restart. A migration that fails
# stops the script before anything is swapped.

set -euo pipefail

APP_DIR=${APP_DIR:-/srv/a1360}
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
BRANCH=${BRANCH:-master}

cd "$APP_DIR"

echo "==> Fetching $BRANCH"
git fetch --quiet origin "$BRANCH"
git checkout --quiet "$BRANCH"
git reset --hard --quiet "origin/$BRANCH"
echo "    now at $(git log --oneline -1)"

echo "==> Installing dependencies"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt

echo "==> Checking configuration"
"$PY" manage.py check --deploy --fail-level ERROR

echo "==> Migrating"
"$PY" manage.py migrate --noinput

echo "==> Syncing permissions"
# Writes any new permission codes and refreshes the pre-built roles. Custom
# roles an admin created are never touched.
"$PY" manage.py seed_permissions

echo "==> Collecting static files"
"$PY" manage.py collectstatic --noinput --clear

echo "==> Restarting"
sudo systemctl restart a1360
sleep 2
systemctl is-active --quiet a1360 && echo "    a1360 is running" || {
    echo "    a1360 FAILED to start:"
    journalctl -u a1360 -n 30 --no-pager
    exit 1
}

echo
echo "==> Release gate"
# Runs against a throwaway test database, not production data.
"$PY" manage.py acceptance || {
    echo
    echo "The acceptance gate did not pass on this box. The site is running,"
    echo "but do not hand it to the pilot crew until this is green."
    exit 1
}

echo
echo "Deployed. Confirm https:// loads and that /sw.js returns 200."
