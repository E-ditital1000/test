# Deploying A1 360 to Railway

The alternative to [deploy-vps.md](deploy-vps.md), and the quicker one: no
server to keep patched, HTTPS by default, and Postgres managed alongside the
app. What it costs you is a machine you can log into — everything below runs
through the Railway dashboard or the CLI.

HTTPS is not optional here, and Railway gives it: the offline layer needs it
or a technician's phone will not cache a thing, and the attendance scanner
needs it or the camera will never start.

## What is in the repository

| File | What it does |
| --- | --- |
| [`railway.json`](../railway.json) | Tells Railway to build with Nixpacks and start with `start.sh` |
| [`start.sh`](../start.sh) | Migrates, seeds the permission list, then runs gunicorn on `$PORT` |
| [`.python-version`](../.python-version) | Pins the interpreter to 3.13, as used in development |
| [`requirements.txt`](../requirements.txt) | Production dependencies only |
| [`requirements-dev.txt`](../requirements-dev.txt) | The above plus pytest, esprima and Playwright |

Nothing in them holds a secret. Every credential is an environment variable
set in Railway, and none of them is ever committed.

## 1. Create the services

In a new Railway project:

1. **+ New → Database → Postgres.**
2. **+ New → GitHub Repo**, pointing at this repository and the branch you
   want deployed.

Railway builds the app service on push. It will fail the first time, because
it has no settings yet. That is expected — do step 2 and redeploy.

## 2. Set the variables

On the **app** service, under Variables. Names on the left are what
`settings.py` reads; nothing here is guessed.

| Variable | Value |
| --- | --- |
| `SECRET_KEY` | Generate one. See below. |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | Your Railway domain, no scheme — e.g. `a1360-production.up.railway.app` |
| `CSRF_TRUSTED_ORIGINS` | The same domain **with** the scheme — `https://a1360-production.up.railway.app` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` — reference the database service, do not paste a password |
| `CLOUDINARY_CLOUD_NAME` | From your Cloudinary dashboard |
| `CLOUDINARY_API_KEY` | From your Cloudinary dashboard |
| `CLOUDINARY_API_SECRET` | From your Cloudinary dashboard |

Generate the signing key with:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

### `DEBUG=False` must be set before the first successful build

This is the one that will bite you. With `DEBUG` off, static files are served
from a hashed manifest that `collectstatic` writes during the build. If the
build runs with `DEBUG` unset it defaults to on, no manifest is written, and
the app then starts with `DEBUG=False` and raises **"Missing staticfiles
manifest entry"** on every page.

Set the variables first, then deploy. If you have already built without it,
set it and redeploy — a fresh build fixes it.

### Cloudinary

Railway will hand you a single `CLOUDINARY_URL` of the form
`cloudinary://<api_key>:<api_secret>@<cloud_name>`. This project reads the
three parts separately, so split it: the cloud name is the part after the
`@`, and the key and secret are either side of the colon before it.

Leave all three blank and uploads fall back to the container's own disk —
which on Railway is wiped on every deploy. Assessment photographs and expense
receipts would not survive the week, so set them before anyone uses the
system in earnest.

## 3. First deploy

Push, or hit Deploy. `start.sh` migrates and seeds the permission list on the
way up, so the database is ready without a manual step.

Then create the first Admin, which is the only thing that cannot be
automated safely:

```bash
railway link            # pick the project, then the app service
railway run python manage.py createsuperuser
```

Sign in at `https://<your-domain>/login/` and, in Settings → Users, give that
account the Admin role and add the rest of the staff.

## 4. Confirm it before letting anyone in

```bash
# Every automated scenario the release gate covers.
railway run python manage.py acceptance
```

Then, on the real domain and a real phone:

- [ ] The sign-in page loads over `https://` with no mixed-content warning
- [ ] Clock in: the QR scanner opens the camera on an Android phone
- [ ] Airplane mode: a clock event queues, then syncs when signal returns
- [ ] An assessment photo uploads and is still there after the next deploy
      (that is the Cloudinary check — if it vanishes, media is on the
      container disk)

Only once `https://` is confirmed working, turn on HSTS:

| Variable | Value |
| --- | --- |
| `SECURE_HSTS_SECONDS` | `31536000` |

It is off by default and deliberately so. HSTS tells every browser to refuse
plain HTTP for this domain for a year, and a browser that has cached that
instruction cannot be told otherwise.

## What to watch

**Keep the web service at one instance.** `start.sh` migrates on boot, and
two containers migrating at once will collide. If you need to scale, move
those two commands into Railway's pre-deploy command, which runs once per
release rather than once per boot, and scale freely after that.

**The private network is runtime-only.** `postgres.railway.internal` does not
resolve during a build, which is why nothing touches the database until
`start.sh` runs. A build step that needs the database will fail, and the
error will not obviously say why.

**Backups are yours to arrange.** Railway snapshots the volume; that is not
the same as a tested restore. The attendance record is a payroll input —
take a `pg_dump` you have actually restored somewhere before go-live.

```bash
railway run pg_dump "$DATABASE_URL" > a1360-$(date +%F).sql
```

## Rolling back

Railway keeps previous deployments: open the failed one's predecessor and
**Redeploy**. That rolls back the code, not the database — a migration that
has run stays run. Every migration in this project has a reverse, so an
unwanted one comes off with `railway run python manage.py migrate <app>
<previous>` before redeploying.
