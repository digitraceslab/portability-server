# Niimport

A service that allows participants in studies to donate their data from third party services
securely and easily.

## Overview

Niimport allows participants in research studies to donate their data securely and easily using
data portability APIS from third party services. Participants authorize the donation once and Niimport
automates the rest. It handles data retrieval, removes an unnecessary data and allows participants
to view and delete their data. Reserchers download the data using an API once it's processed.

Niimport is not a data storage service. Data is encrypted at rest and deleted as soon as the
researcher confirms it has been downloaded. Niimport handles authorization, data transfer and minimization.


## How it works

1. A researcher creates a donation request through the API, specifying the data types and date range
   they require. Niimport generates a unique donation URL for the participant.
2. The researcher sends the donation URL to the participant. The participant clicks on the link, which
   takes them to Niimport's donation page.
3. The participant approves Niimport's Terms of Service and Privacy Notice, and then authorizes the 
   transfer using the third party service's OAuth flow.
4. Once authorized, Niimport requests a data export. Once the export is ready, Niimport downloads the data.
5. Niimport removes any data outside the date range specified by the researcher and any data types that
   are not requested. Once processed, the participant can review the data. They can choose to delete
   the data at any point.
6. The researcher uses the API to dowload processed data. Once the researcher confirms they have
   downloaded the data, Niimport deletes it permanently.


## Features

Data types we currently support
 - Google Portability data
   - YouTube history
   - Google Search history
   - Google Discover history
   - Google Lens history
   - Google Play Games activity
   - Google Play Store activity
   - Google Image Search history
   - Google Video Search history



## Researcher API

All API requests require a researcher token in the header:

```
Authorization: Token <researcher_token>
```

The researcher token is created by an administrator using the management command:

```bash
python manage.py create_researcher_token
```


### Create a donation

```
POST /api/donations/
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_type` | string | yes | `google_portability` or `tiktok_portability` |
| `data_start_date` | date | no | Only include data from this date onward (YYYY-MM-DD) |
| `data_end_date` | date | no | Only include data up to this date (YYYY-MM-DD) |
| `requested_data_types` | list | no | Data types to collect. Empty means all available |

Available Google data types: `youtube_history`, `discover`, `google_lens`, `google_play_games`, `google_play_store`, `image_search`, `search`, `video_search`.

Returns a donation object including a `donation_url` — an absolute URL to send directly to the participant to begin the OAuth flow.

Example
``` bash
curl -X POST http://localhost:8000/api/donations/ \
  -H "Authorization: Token <researcher_token>" \
   -H "Content-Type: application/json" \
   -d '{
     "source_type": "google_portability",
     "data_start_date": "2023-01-01",
     "data_end_date": "2023-12-31",
     "requested_data_types": ["youtube_history", "search"]
   }'
```

### List donations

```
GET /api/donations/
```

Returns all donations created by the researcher.

Example
``` bash
curl -X GET http://localhost:8000/api/donations/ \
  -H "Authorization: Token <researcher_token>"
```

### Get donation status

```
GET /api/donations/<id>/
```

Returns donation details including `status`: `pending`, `authorized`, `processing`, `processed`, or `error`.

Example
``` bash
curl -X GET http://localhost:8000/api/donations/<id>/ \
   -H "Authorization: Token <researcher_token>"
```

### Query donation data

```
GET /api/donations/<id>/data/
```

Without parameters, returns available `data_types`. With a `data_type`, returns the data:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `data_type` | string | no | Which data type to retrieve |
| `start_date` | date | no | Filter rows from this date (YYYY-MM-DD) |
| `end_date` | date | no | Filter rows up to this date (YYYY-MM-DD) |
| `limit` | integer | no | Max rows to return (default: 1000) |
| `offset` | integer | no | Skip this many rows (default: 0) |

Example
``` bash
curl -X GET "http://localhost:8000/api/donations/<id>/data/?data_type=youtube_history&start_date=2023-01-01&end_date=2023-12-31
&limit=100&offset=0" \
   -H "Authorization: Token <researcher_token>"
```

### Signal that a donation may be deleted

```
POST /api/donations/<id>/can-delete/
```

Records that a verified copy of the data is held. Nothing is deleted at that
point: it starts a shorter retention clock, after which the service deletes the
donation itself. Repeating the call keeps the first time.

Example
``` bash
curl -X POST http://localhost:8000/api/donations/<id>/can-delete/ \
   -H "Authorization: Token <researcher_token>"
```

### Delete a donation

```
DELETE /api/donations/<id>/
```

Revokes OAuth access and deletes the donation and its data.

Example
``` bash
curl -X DELETE http://localhost:8000/api/donations/<id>/ \
   -H "Authorization: Token <researcher_token>"
```


# Generating documentation

API documentation and some additional information can be found in `docs/` and built using sphinx.
To build the documentation, install the dependenciesn and run sphinx:

```bash
pip install -r docs/requirements.txt
cd docs
make html
```


# Deployment

## Before deploying

Before deploying to production, you must:

1. **Update Terms of Service and Privacy Notice** — review `templates/donations/terms_of_service.html`
   and `templates/donations/privacy_notice.html`. Update contact information, and any
   institution-specific details.

2. **Request Google Data Portability API access** — apply through the
   [Google API Console](https://console.cloud.google.com/). You will need:
   - Deployed application with the intended URL.
        - Reviewers will check privacy notice and terms of service.
        - They will test the OAuth flow on the website.
   - OAuth consent screen configured with the correct scopes on the [Google Cloud Console](https://console.cloud.google.com/).
   - **A Cloud Application Security Assessment (CASA)** may be required for restricted scopes
      - If this is required, you will receive a request at the end of the API review. The assesment must
        be done by a third party vendor and can take 4-6 weeks and typically cost between $500 and $3000.

3. **Request TikTok Data Portability API access** — apply through the
   [TikTok Developer Portal](https://developers.tiktok.com/). You will need:
   - Deployed application with the intended URL. The URL must contain the name of the service (e.g. `myportability.labname.com`).
      - A privacy policy and terms of service accessible at the URL.
      - They will test the OAuth flow, which requires sandbox mode set up at [TikTok Developer Portal](https://developers.tiktok.com/).
   - Web application set up on the [TikTok Developer Portal](https://developers.tiktok.com/).

4. **Set up OAuth credentials** — add the client IDs and secrets to your `.env` file.

## Prerequisites

- Python 3.12+
- PostgreSQL
- Redis

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/digitraceslab/portability-server.git
   cd portability-server
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   Or with mamba:
   ```bash
   mamba create -n portability-server python=3.12 pip -y
   mamba activate portability-server
   pip install -r requirements.txt
   ```

3. **Set up PostgreSQL**
   ```bash
   sudo -u postgres createuser portability_user -P
   sudo -u postgres createdb portability_db -O portability_user
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your database credentials, OAuth keys, etc.
   ```

5. **Run migrations**
   ```bash
   python manage.py migrate
   ```

6. **Create a researcher API token**
   ```bash
   python manage.py create_researcher_token
   ```

## Running

Start all three processes for local development:

```bash
# Django development server
python manage.py runserver

# Celery worker (in a separate terminal)
celery -A portability_server worker -l info

# Celery beat scheduler (in a separate terminal)
celery -A portability_server beat -l info
```

## Deployment

The `deploy/` templates and `scripts/` in this repository target a single-domain server. Hosts
serving multiple domains, or otherwise running a customised nginx configuration, should keep
managing nginx by hand instead — leave `INSTALL_CONFIGS` unset (or `no`) so the scripts never
touch it.

### Automated setup

After cloning the repository and creating `.env` from `.env.example` (see below), the standard
way to perform a first-time deployment is:

```bash
cp .env.example .env
# Edit .env with production values: DEBUG=False, proper SECRET_KEY, ALLOWED_HOSTS, etc.
./scripts/deploy.sh
```

`scripts/deploy.sh` installs the system packages, creates the virtualenv, installs dependencies
from `requirements.txt` (hash-verified by pip), optionally provisions the PostgreSQL role and
database, validates the environment configuration, runs migrations and `collectstatic`, and
installs and starts the systemd services and nginx configuration described in the sections below.
Being a first-time setup, it always installs the rendered configuration (`INSTALL_CONFIGS=yes`).

Database provisioning is controlled by `SETUP_DB`: `auto` (the default) provisions the database
locally using passwordless peer authentication via `sudo` when available, otherwise prompts
interactively for the PostgreSQL admin password (this also works against a remote database), and
skips provisioning with a warning if an admin connection can't be reached; `yes` behaves the same
but fails instead of skipping; `no` skips database provisioning entirely. `DB_ADMIN_USER`
(default `postgres`) selects the admin role used for provisioning. On a fresh PostgreSQL
install where the admin role has no password yet, the script prompts to set one so the
instance is not left with a passwordless superuser.

The scripts never modify an existing `.env`, and per-deployment values such as the domain and
credentials are never version-controlled — the nginx domain is derived from `ALLOWED_HOSTS` in
`.env`, and TLS certificate paths default to the Let's Encrypt layout, all overridable via the
`DOMAIN`, `SSL_CERT` and `SSL_KEY` environment variables.

### Updating

`scripts/update.sh` handles routine updates: it aborts if the checkout has local changes,
fast-forward pulls the latest commit, reinstalls dependencies (hash-verified), re-validates the
environment configuration, runs migrations and `collectstatic`, re-renders the systemd and nginx
configuration from `deploy/`, and restarts the services, verifying each one is active afterwards.

Whether the re-rendered systemd/nginx configuration is actually installed is controlled by
`INSTALL_CONFIGS` (default `no`): if a rendered file differs from what's installed, `no` prints a
`diff -u` of the drift and leaves the installed file untouched, while `yes` applies it. This keeps
`update.sh` safe to run on servers where nginx (or a service unit) has been hand-edited after the
initial `deploy.sh` run — set `INSTALL_CONFIGS=yes` once you've reviewed the reported diff and want
it applied.

Deployment paths are read from `.env` when set: `APP_DIR` (default: the repository root
containing the script) and `VENV_PATH` (default: `$APP_DIR/venv`). Either can also be
overridden as an environment variable for a single run.

```bash
./scripts/update.sh
```

**Permissions.** `scripts/update.sh` also creates the service user (`RUN_USER`, default
`portability`) if it doesn't exist yet and applies the read/write split described in "Create the
service user" below: read-only ACLs on the checkout and venv, ownership of `data/` and the archive
directory. Running `INSTALL_CONFIGS=yes scripts/update.sh` on an existing deployment migrates it to
the dedicated user in one step.

### System packages

```bash
sudo apt update
sudo apt install python3 python3.12-venv postgresql nginx-extras redis-server clamav clamav-daemon acl
```

`nginx-extras` (rather than plain `nginx`) is required because it provides the
`headers-more-nginx-module`, which `deploy/nginx-site.conf` relies on for its `more_*` header
directives.

### Application setup

```bash
git clone https://github.com/digitraceslab/portability-server.git /opt/portability-server
cd /opt/portability-server

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with production values: DEBUG=False, proper SECRET_KEY, ALLOWED_HOSTS, etc.

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py create_researcher_token
```

### Create the service user

The units below run as a dedicated, unprivileged system account, not the account used to deploy:

```bash
sudo adduser --system --group --no-create-home --shell /usr/sbin/nologin portability
```

It owns only `data/` and the archive directory — the locations the services actually write to.
Everything else (the checkout, the venv, `.env`) is read via POSIX ACLs granted to this account, so
it cannot modify application code or settings even if a service process is compromised. The Celery
beat schedule file lives under `/var/lib/portability`, managed by systemd's `StateDirectory=`, not
inside the checkout.

### Gunicorn service

`scripts/deploy.sh` and `scripts/update.sh` render this from `deploy/portability-gunicorn.service`
and install it to `/etc/systemd/system/portability-gunicorn.service`:

```ini
[Unit]
Description=portability-server gunicorn
After=network.target

[Service]
User=portability
Group=portability
UMask=077
ImportCredential=portability.*
WorkingDirectory=/opt/portability-server
ExecStart=/opt/portability-server/venv/bin/gunicorn --access-logfile - --workers 3 --timeout 120 --bind unix:/run/portability/portability-server.sock portability_server.wsgi:application
RuntimeDirectory=portability

[Install]
WantedBy=multi-user.target
```

### Celery worker service

`scripts/deploy.sh` and `scripts/update.sh` render this from
`deploy/portability-celery-worker.service` and install it to
`/etc/systemd/system/portability-celery-worker.service`:

```ini
[Unit]
Description=portability-server celery worker
After=network.target redis-server.service

[Service]
User=portability
Group=portability
UMask=077
ImportCredential=portability.*
WorkingDirectory=/opt/portability-server
ExecStart=/opt/portability-server/venv/bin/celery -A portability_server worker -l info
Restart=always

[Install]
WantedBy=multi-user.target
```

### Celery beat service

`scripts/deploy.sh` and `scripts/update.sh` render this from
`deploy/portability-celery-beat.service` and install it to
`/etc/systemd/system/portability-celery-beat.service`:

```ini
[Unit]
Description=portability-server celery beat
After=network.target redis-server.service

[Service]
User=portability
Group=portability
UMask=077
ImportCredential=portability.*
StateDirectory=portability
WorkingDirectory=/opt/portability-server
ExecStart=/opt/portability-server/venv/bin/celery -A portability_server beat -l info --schedule=/var/lib/portability/celerybeat-schedule
Restart=always

[Install]
WantedBy=multi-user.target
```

### Enable and start services

`scripts/deploy.sh` does this automatically (and `scripts/update.sh` restarts the services after
an update); the equivalent manual command is:

```bash
sudo systemctl enable --now portability-gunicorn portability-celery-worker portability-celery-beat
```

### Virus scanning

`freshclam` updates ClamAV's virus signatures automatically. Uploaded and downloaded export
archives can be large, so raise the scan limits in `/etc/clamav/clamd.conf`:

```
MaxFileSize 4000M
MaxScanSize 4000M
```

Restart the daemon to apply:

```bash
sudo systemctl restart clamav-daemon
```

ClamAV does not support fully scalling files larger than 4000M. Larger files are still scanned partially. 

When scanning is enabled (`CLAMAV_ENABLED=True`), the app rejects uploads and downloads if the
`clamd` daemon is unreachable (fail closed).

### Nginx

`scripts/deploy.sh` and `scripts/update.sh` render this from `deploy/nginx-site.conf` and install
it to `/etc/nginx/sites-available/portability-server`. The `limit_req_zone` directive can't live
in a server block, so it ships separately as `deploy/nginx-ratelimit.conf`, installed to
`/etc/nginx/conf.d/portability-ratelimit.conf` (included from the `http` context automatically by
most nginx installs).

Set `DOMAINS` in `.env` to a comma-separated list to serve more than one name. The template is
rendered once per domain, and each rendered block takes its certificate from
`/etc/letsencrypt/live/<domain>/`. With `DOMAINS` unset the first entry of `ALLOWED_HOSTS` is
used, which is the single-domain case below. `SSL_CERT` and `SSL_KEY` override the certificate
paths for every rendered block, so they only make sense with a single domain.


```nginx
limit_req_zone $binary_remote_addr zone=portability:10m rate=10r/s;

server {
    listen 80;
    server_name DOMAIN;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name DOMAIN;

    ssl_certificate /PATH_TO/fullchain.pem;
    ssl_certificate_key /PATH_TO/privkey.pem;
    client_max_body_size 55G;

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        alias /opt/portability-server/staticfiles/;
    }

    location / {
        limit_req zone=portability burst=20 nodelay;
        include proxy_params;
        proxy_pass http://unix:/run/portability/portability-server.sock;
    }
}
```

The scripts do this automatically, including the symlink into `sites-enabled` and
`nginx -t && systemctl reload nginx`; the equivalent manual commands are:

```bash
sudo ln -s /etc/nginx/sites-available/portability-server /etc/nginx/sites-enabled
sudo nginx -t && sudo systemctl restart nginx
```

### Virus scanning

Archives are scanned before they are read. The scanner's own limits decide
whether that means anything: by default clamd skips files past 25 MB and
reports them clean, so `deploy/clamd-settings.conf` records the settings this
service requires, and `scripts/deploy.sh`/`scripts/update.sh` merge them into
`/etc/clamav/clamd.conf`.

They are merged rather than installed as a file because clamd has no include
directive, and the packaged `clamd.conf` is regenerated by
`dpkg-reconfigure clamav-daemon`. `scripts/verify.sh` therefore compares what
clamd reports as being in effect against the required values, and reports a
problem when they differ or when `clamconf` is unavailable.

Because a reverted setting is silent - large files are simply recorded as
clean - the verification is worth running often:

```
15 * * * * VERIFY_EMAIL=you@example.org /home/USER/portability-server/scripts/verify.sh >/dev/null
```

### Retention

Donated data is kept for `RETENTION_DAYS` from the moment it arrives, which
defaults to 14 days and mirrors how long Google keeps an export available, so
no copy is held longer than the source keeps its own. When a researcher signals
that they hold a verified copy, a shorter clock of `CAN_DELETE_RETENTION_DAYS`
applies instead. Whichever expires first decides.

A scheduled task checks twice a day, deletes what has expired - revoking the
platform grant first, where there is one - and mails the administrators what it
deleted and what is due within `RETENTION_WARNING_DAYS`.

### Key management with OpenBao

The encryption key that wraps Parquet data keys and OAuth tokens should not
sit on the application host. In production it is held by an
[OpenBao](https://openbao.org/) transit engine running on a separate,
university-managed host; the application only ever holds short-lived AppRole
credentials, delivered as root-only files by systemd.

**1. Set up the vault host.** Use a separate VM (e.g. an Aalto VM) with
inbound access denied to everything except TCP 8200 from the application
host.

```bash
# Install OpenBao (verify the checksum against the release page)
curl -LO https://github.com/openbao/openbao/releases/download/<version>/bao_<version>_linux_amd64.deb
sha256sum bao_<version>_linux_amd64.deb   # compare against the published checksum
sudo dpkg -i bao_<version>_linux_amd64.deb
```

`/etc/openbao/openbao.hcl`:

```hcl
listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/etc/openbao/tls/cert.pem"
  tls_key_file  = "/etc/openbao/tls/key.pem"
}

storage "file" {
  path = "/var/lib/openbao"
}

disable_mlock = false  # the openbao unit needs CAP_IPC_LOCK
```

Initialize with a single key share, since this is not a multi-operator
setup:

```bash
bao operator init -key-shares=1 -key-threshold=1
```

Store the unseal key at `/etc/openbao/unseal.key`, root-owned, mode 0400, and
the root token in the university password manager. OpenBao seals itself on
every restart, so auto-unseal it with a small systemd unit:

```ini
# /etc/systemd/system/openbao-unseal.service
[Unit]
Description=Unseal OpenBao
After=openbao.service

[Service]
Type=oneshot
Environment=BAO_ADDR=https://127.0.0.1:8200
Environment=BAO_CACERT=/etc/openbao/tls/cert.pem
ExecStart=/bin/sh -c 'bao operator unseal "$(cat /etc/openbao/unseal.key)"'

[Install]
WantedBy=multi-user.target
```

Enable it with `systemctl enable openbao-unseal.service` so it runs on every
boot after the vault itself.

This is a file-based unseal: whoever can read the vault host's disk or its
backups holds the unseal key. There is no TPM available on this host to do
better, so the vault host's isolation (no inbound access beyond the transit
port) is what actually protects the key.

**2. Configure the transit engine and an AppRole for the application.**

```bash
bao secrets enable transit
bao write -f transit/keys/portability
```

A policy restricting access to just this key's encrypt/decrypt operations,
`portability-policy.hcl`:

```hcl
path "transit/encrypt/portability" {
  capabilities = ["update"]
}
path "transit/decrypt/portability" {
  capabilities = ["update"]
}
```

```bash
bao policy write portability portability-policy.hcl
bao auth enable approle
bao write auth/approle/role/portability \
    token_policies=portability \
    token_ttl=1h \
    token_max_ttl=24h \
    secret_id_bound_cidrs=<app-host-ip>/32 \
    token_bound_cidrs=<app-host-ip>/32 \
    secret_id_num_uses=0

bao read auth/approle/role/portability/role-id
bao write -f auth/approle/role/portability/secret-id
```

**3. Enable audit logging**, so every wrap/unwrap is recorded:

```bash
bao audit enable file file_path=/var/log/openbao/audit.log
```

**4. Configure the application host.** Set `OPENBAO_ADDR` (and
`OPENBAO_CACERT` if the vault uses an internal CA) in `.env`, then run
`scripts/update.sh`. It prompts for the role id and secret id and writes them
to `/etc/credstore/portability.openbao_role_id` and
`/etc/credstore/portability.openbao_secret_id`; the systemd units pick them
up via `ImportCredential=portability.*`.

- **Rotation:** `bao write -f transit/keys/portability/rotate` — OpenBao
  keeps old key versions for decrypting existing ciphertexts, so no
  re-encryption of stored data is needed.
- **Revocation:** `bao write -f auth/approle/role/portability/secret-id-accessor/destroy`
  (or delete the role entirely) immediately invalidates the application's
  access.

**Fallback without a vault.** With `OPENBAO_ADDR` left empty, the service
runs on a Fernet key held in `/etc/credstore/portability.encryption_key`
instead — still root-only and delivered by systemd, but not isolated from
the application host. `scripts/deploy.sh`, `scripts/update.sh` and
`scripts/verify.sh` all report this loudly on every run.

## How data is stored

An archive - a Google export or a participant's upload - is written to
`ARCHIVE_DIR` as it arrives, scanned, read, and deleted, whether reading
succeeded or failed.

Processed data is stored as encrypted Parquet under `data/<donation>/<source>/`,
one directory per data type. While an export is being ingested each archive
contributes its own file, and when the last archive is read they are combined
into a single `combined.parquet` in timestamp order.

Encryption is Parquet Modular Encryption (AES-GCM) with an encrypted footer, so
metadata and column statistics are unreadable without the key and tampering is
detected on read. Parquet's per-file data keys are wrapped and unwrapped by an
OpenBao transit engine on a separate host (see "Key management with OpenBao"
below); OAuth tokens are encrypted through the same engine. The application
never holds the master key. When no vault is configured the service falls
back to a locally held Fernet key, still delivered as a root-only file by
systemd rather than kept in `.env`. Row groups bound the cost of a request: a
page decrypts only the group holding it, and a date filter skips groups whose
recorded timestamp range cannot match.

Archives are not encrypted during processing. They exist only during processing, and a scheduled task removes stale files left over
for example from a crash during processing.

## Environment Variables

All configuration is done via `.env` (copy from `.env.example`):

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Django secret key | `change-me-to-a-random-secret-key` |
| `DEBUG` | Enable debug mode | `True` / `False` |
| `ALLOWED_HOSTS` | Comma-separated allowed hostnames | `localhost,127.0.0.1` |
| `DATABASE_URL` | PostgreSQL connection string | `postgres://portability_user:password@localhost:5432/portability_db` |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth 2.0 client ID | |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth 2.0 client secret | |
| `TIKTOK_CLIENT_KEY` | TikTok API client key | |
| `TIKTOK_CLIENT_SECRET` | TikTok API client secret | |
| `OPENBAO_ADDR` | Address of the OpenBao server holding the encryption key; empty runs on a locally held, root-delivered key instead | `https://vault.example:8200` |
| `OPENBAO_MOUNT` | Mount path of the transit secrets engine | `transit` |
| `OPENBAO_KEY_NAME` | Name of the transit key used to wrap data keys and OAuth tokens | `portability` |
| `ADMIN_ALLOWED_CIDRS` | Comma-separated networks allowed to reach `/admin/` and the researcher API under `/api/`; empty makes both unreachable through nginx | `130.233.0.0/16,10.0.0.0/8` |
| `OPENBAO_CACERT` | Path to a CA certificate for the vault's TLS, if not publicly trusted | |
| `CELERY_BROKER_URL` | Redis URL for Celery task broker | `redis://localhost:6379/1` |
| `CELERY_RESULT_BACKEND` | Redis URL for Celery result storage | `redis://localhost:6379/1` |
| `CACHE_URL` | Redis URL for the Django cache (rate-limit counters) | `redis://localhost:6379/2` |
| `UPLOAD_MAX_BYTES` | Maximum accepted upload size in bytes (default 55 GB) | |
| `CLAMAV_ENABLED` | Scan ingested files with ClamAV (clamdscan); default enabled when `DEBUG=False` | `True` / `False` |
| `DOMAINS` | Domains nginx serves, comma-separated; defaults to the first `ALLOWED_HOSTS` entry | `a.example,b.example` |
| `ARCHIVE_DIR` | Where archives are held while being processed (default `data/archives`) | |
| `ARCHIVE_MAX_AGE_SECONDS` | Age at which an abandoned archive is deleted (default 86400) | |
| `CELERY_TASK_TIME_LIMIT` | Seconds a processing task may run (default 21600) | |
| `RETENTION_DAYS` | Days donated data is kept after it arrives (default 14) | |
| `CAN_DELETE_RETENTION_DAYS` | Days kept after the researcher confirms a verified copy (default 2) | |
| `RETENTION_WARNING_DAYS` | Unflagged donations expiring within this many days are named in the daily mail (default 2) | |
| `EMAIL_FROM` | Sender for administrator mail; must be an `aalto.fi` address | `portability@aalto.fi` |
| `ADMIN_EMAILS` | Comma-separated recipients of the daily retention mail | |

## Testing

```bash
python manage.py test
```
