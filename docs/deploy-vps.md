# Deploying A1 360 to a VPS

Ubuntu 22.04/24.04 or Debian 12. One box: nginx in front, gunicorn behind a
unix socket, Postgres locally.

**HTTPS is not optional.** The service worker that makes the field surfaces
work with no signal will not register over plain HTTP. On `http://` a
technician silently loses offline page loads — the one capability the pilot
exists to prove. Do not hand the box to a crew until `https://` works.

---

## 1 · Packages and a user

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev build-essential \
                    postgresql postgresql-contrib nginx git \
                    libpq-dev certbot python3-certbot-nginx

# The app runs as its own user with no login shell.
sudo adduser --system --group --home /srv/a1360 --shell /usr/sbin/nologin a1360
sudo usermod -aG www-data a1360
```

## 2 · Database

```bash
sudo -u postgres psql <<'SQL'
CREATE USER a1360 WITH PASSWORD 'use-a-real-one';
CREATE DATABASE a1360 OWNER a1360;
ALTER ROLE a1360 SET client_encoding TO 'utf8';
ALTER ROLE a1360 SET default_transaction_isolation TO 'read committed';
ALTER ROLE a1360 SET timezone TO 'UTC';
SQL
```

## 3 · Code and virtualenv

```bash
sudo -u a1360 git clone https://github.com/E-ditital1000/test.git /srv/a1360
cd /srv/a1360
sudo -u a1360 python3 -m venv .venv
sudo -u a1360 .venv/bin/pip install --upgrade pip
sudo -u a1360 .venv/bin/pip install -r requirements.txt
sudo -u a1360 mkdir -p media staticfiles
```

## 4 · Environment

```bash
sudo -u a1360 cp deploy/a1360.env.example /srv/a1360/.env
sudo -u a1360 .venv/bin/python -c \
  "from django.core.management.utils import get_random_secret_key as k; print(k())"
sudo -u a1360 nano /srv/a1360/.env        # paste the key, set the rest
sudo chmod 600 /srv/a1360/.env
```

`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` must both name the real domain, or
every form POST will be refused with a CSRF error that looks like a bug.

## 5 · First run

```bash
cd /srv/a1360
sudo -u a1360 .venv/bin/python manage.py migrate
sudo -u a1360 .venv/bin/python manage.py seed_permissions
sudo -u a1360 .venv/bin/python manage.py collectstatic --noinput
sudo -u a1360 .venv/bin/python manage.py createsuperuser
```

Do **not** run `seed_demo_data` on a box that will hold real records. It
creates nineteen accounts with a shared password.

## 6 · systemd and nginx

```bash
sudo cp deploy/a1360.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now a1360
systemctl status a1360

sudo cp deploy/nginx.conf /etc/nginx/sites-available/a1360
sudo sed -i 's/a1360.example.com/YOUR-DOMAIN/g' /etc/nginx/sites-available/a1360
sudo ln -s /etc/nginx/sites-available/a1360 /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 7 · TLS

```bash
sudo certbot --nginx -d YOUR-DOMAIN
sudo systemctl status certbot.timer     # renewal is automatic
```

Once `https://` is confirmed working — **and not before** — set
`SECURE_HSTS_SECONDS=31536000` in `.env` and restart. HSTS tells every
browser to refuse plain HTTP for this domain for a year, and a browser that
has cached that instruction cannot be told otherwise.

## 8 · Verify

```bash
curl -I https://YOUR-DOMAIN/login/     # 200
curl -I https://YOUR-DOMAIN/sw.js      # 200, Content-Type application/javascript
curl -I http://YOUR-DOMAIN/            # 301 to https
sudo -u a1360 /srv/a1360/.venv/bin/python manage.py acceptance
```

In a browser, on the real domain:

- [ ] DevTools → Application → Service Workers shows **activated**
- [ ] The sign-in page shows **no** demo-account panel (it is off when `DEBUG=False`)
- [ ] Sign in, open `/field-jobs/`, then go offline and reload — the page still renders
- [ ] Submit an assessment with photographs — it must not 413

## Updating

```bash
sudo -u a1360 /srv/a1360/deploy/deploy.sh
```

Fetches, installs, checks, migrates, collects, restarts, then runs the
acceptance gate. A failed migration stops it before anything is swapped.

Give the `a1360` user permission to restart only its own service:

```bash
echo 'a1360 ALL=(root) NOPASSWD: /bin/systemctl restart a1360' \
  | sudo tee /etc/sudoers.d/a1360
sudo chmod 440 /etc/sudoers.d/a1360
```

## Backups

Two things matter and they are separate:

```bash
# The database — every ticket, assessment and attendance event.
sudo -u postgres pg_dump a1360 | gzip > /var/backups/a1360-$(date +%F).sql.gz

# Uploaded files, unless CLOUDINARY_* is set, in which case they are not here.
tar czf /var/backups/a1360-media-$(date +%F).tar.gz -C /srv/a1360 media
```

Put both in cron and copy them **off the box**. A backup on the same disk as
the thing it backs up is not a backup.

## Things that will bite

| Symptom | Cause |
| --- | --- |
| Endless redirect loop | nginx is not sending `X-Forwarded-Proto`; check the `proxy_set_header` line |
| CSRF failed on every form | `CSRF_TRUSTED_ORIGINS` missing the scheme, or not naming the real domain |
| 413 on assessment submit | `client_max_body_size` below 25M, or `DATA_UPLOAD_MAX_MEMORY_SIZE` lowered |
| Offline reload shows the browser error page | Site is on `http://`, so the service worker never registered |
| Phones keep serving an old release | `/sw.js` is being cached; the nginx block sets `no-store` for it |
| 502 after deploy | gunicorn failed to start — `journalctl -u a1360 -n 50` |
| Static files 404 | `collectstatic` not run, or nginx `alias` path wrong |

## Sizing

Three gunicorn workers and Postgres fit comfortably in 2 GB. The rule of
thumb is `(2 × cores) + 1` workers; raise it only if you have the RAM, since
each worker is a full Python process.
