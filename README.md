# Portability Server

Service that manages and stores donated research data from third party platforms.

## Overview

The service allows a user to securely donate data from third party services to research projects.
Participants receive a link with an authentication token. The link takes them to a donation page where
they can authenticate with the third party service and give access to download their data.

The service processes the data and removes data the study does not need. For example, data from before
the specified study period and data types not require for the study are removed. The participant can
use their token to later see their data and ask for deletion.

Note that the researcher may have downloaded any data deleted from the server.

Researchers use an API to generate participant tokens to send to each participant. They can check
the processing status of each donation and download data once processed.


## Features

Data types we currently support
 - Google Portability data


## Researcher API

All API requests require a researcher token in the header:

```
Authorization: Token <researcher_token>
```

Machine-readable endpoint documentation is also available at `/api/docs/` (no authentication required).

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

Returns a donation object including a `token` (UUID). Send the participant to `/donate/<token>/` to begin the OAuth flow.

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

Returns all donations created by the authenticated researcher.

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


# Deployment

## Before deploying

Before deploying to production, you must:

1. **Update Terms of Service and Privacy Notice** — review `templates/donations/terms_of_service.html`
   and `templates/donations/privacy_notice.html`. Update contact information, age of consent, and any
   institution-specific details.

2. **Request Google Data Portability API access** — apply through the
   [Google API Console](https://console.cloud.google.com/). You will need:
   - A published privacy policy (served at `/privacy/`)
   - OAuth consent screen configured with the correct scopes
   - A Cloud Application Security Assessment (CASA) may be required for restricted scopes

3. **Request TikTok Data Portability API access** — apply through the
   [TikTok Developer Portal](https://developers.tiktok.com/). You will need:
   - UX mockups showing the data donation flow
   - A description of your data protection policies
   - Documentation of how users can make data subject requests

4. **Set up OAuth credentials** — once approved, add the client IDs and secrets to your `.env` file.

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
sudo apt install python3 python3.12-venv postgresql nginx redis-server
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

### Nginx

Create `/etc/nginx/sites-available/portability-server`:

```nginx
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

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        alias /opt/portability-server/staticfiles/;
    }

    location / {
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

## Testing

```bash
python manage.py test donations
```

## License

<!-- TODO: Choose and add license -->
