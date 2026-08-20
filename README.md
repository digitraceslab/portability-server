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

### System packages

```bash
sudo apt update
sudo apt install python3 python3.12-venv postgresql nginx redis-server clamav clamav-daemon
```

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

### Gunicorn service

Create `/etc/systemd/system/portability-gunicorn.service`:

```ini
[Unit]
Description=portability-server gunicorn
After=network.target

[Service]
User=USERNAME
Group=USERNAME
WorkingDirectory=/opt/portability-server
ExecStart=/opt/portability-server/venv/bin/gunicorn --access-logfile - --workers 3 --bind unix:/run/portability-server/portability-server.sock portability_server.wsgi:application
RuntimeDirectory=portability-server

[Install]
WantedBy=multi-user.target
```

### Celery worker service

Create `/etc/systemd/system/portability-celery-worker.service`:

```ini
[Unit]
Description=portability-server celery worker
After=network.target redis-server.service

[Service]
User=USERNAME
Group=USERNAME
WorkingDirectory=/opt/portability-server
ExecStart=/opt/portability-server/venv/bin/celery -A portability_server worker -l info
Restart=always

[Install]
WantedBy=multi-user.target
```

### Celery beat service

Create `/etc/systemd/system/portability-celery-beat.service`:

```ini
[Unit]
Description=portability-server celery beat
After=network.target redis-server.service

[Service]
User=USERNAME
Group=USERNAME
WorkingDirectory=/opt/portability-server
ExecStart=/opt/portability-server/venv/bin/celery -A portability_server beat -l info --schedule=/opt/portability-server/celerybeat-schedule
Restart=always

[Install]
WantedBy=multi-user.target
```

### Enable and start services

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

Create `/etc/nginx/sites-available/portability-server`. The `limit_req_zone` directive belongs in
the `http` context (e.g. `/etc/nginx/nginx.conf`), so add it there if this site config is not
already included from within `http { ... }`:

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
        proxy_pass http://unix:/run/portability-server/portability-server.sock;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/portability-server /etc/nginx/sites-enabled
sudo nginx -t && sudo systemctl restart nginx
```

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
| `ENCRYPTION_KEY` | Base64 urlsafe Fernet key for data at rest; falls back to `SECRET_KEY` if empty | |
| `CELERY_BROKER_URL` | Redis URL for Celery task broker | `redis://localhost:6379/1` |
| `CELERY_RESULT_BACKEND` | Redis URL for Celery result storage | `redis://localhost:6379/1` |
| `CACHE_URL` | Redis URL for the Django cache (rate-limit counters) | `redis://localhost:6379/2` |
| `UPLOAD_MAX_BYTES` | Maximum accepted upload size in bytes (default 55 GB) | |
| `CLAMAV_ENABLED` | Scan ingested files with ClamAV (clamdscan); default enabled when `DEBUG=False` | `True` / `False` |

## Testing

```bash
python manage.py test
```
