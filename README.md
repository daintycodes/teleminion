# TeleMinion

A self-hosted, Dockerized automation system to download Audio/PDFs from Telegram channels to MinIO storage.

## Features

- 📥 **Automated Discovery**: Background scanner finds audio and PDF files in your Telegram channels
- 🎯 **Manual Approval**: Review and approve files before downloading
- 🚀 **Sequential Processing**: Downloads one at a time to prevent OOM crashes
- 📦 **MinIO Storage**: Files are uploaded to S3-compatible MinIO buckets
- 🔄 **Real-time Dashboard**: HTMX-powered dashboard with live status updates
- 🐳 **Docker Ready**: Single container with PostgreSQL session storage (no SQLite!)

## Quick Start

### 1. Get Telegram API Credentials

1. Go to [my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Go to "API development tools"
4. Create a new application
5. Note your `api_id` and `api_hash`

### 2. Configure Environment

```bash
# Copy the example env file
cp .env.example .env

# Edit with your Telegram credentials
nano .env
```

Set these required variables:
```
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+1234567890
```

### 3. Start with Docker Compose

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f app
```

### 4. Access the Dashboard

Open http://localhost:8000 in your browser.

On first run, you'll need to authenticate:
1. Click "Send Verification Code"
2. Check your Telegram app for the code
3. Enter the code in the dashboard
4. If you have 2FA enabled, enter your password

### 5. Add Channels

1. Go to the "Channels" tab
2. Enter a channel username (e.g., `@channelname`) or invite link
3. Click "Add Channel"
4. The scanner will automatically find audio/PDF files

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Application                   │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Dashboard  │  │   Scanner    │  │   Worker     │  │
│  │   (HTMX)     │  │  (Background)│  │  (Queue)     │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│         │                  │                  │         │
│         └──────────────────┼──────────────────┘         │
│                            │                             │
│  ┌─────────────────────────┴─────────────────────────┐  │
│  │              PostgresSession (Custom)              │  │
│  │         (No SQLite = No Locking Issues)            │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │ Telegram │        │ Postgres │        │  MinIO   │
   │   API    │        │    DB    │        │ Storage  │
   └──────────┘        └──────────┘        └──────────┘
```

## File Status Flow

```
PENDING → QUEUED → DOWNLOADING → UPLOADING → COMPLETED
                                           ↘ FAILED
```

- **PENDING**: File discovered, awaiting approval
- **QUEUED**: Approved, waiting in download queue
- **DOWNLOADING**: Being downloaded from Telegram
- **UPLOADING**: Being uploaded to MinIO
- **COMPLETED**: Stored in MinIO successfully
- **FAILED**: Error occurred, can be retried

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard |
| `/health` | GET | Health check |
| `/pending` | GET | Pending files table |
| `/active` | GET | Active downloads table |
| `/history` | GET | Completed/failed files |
| `/channels` | GET/POST | List/Add channels |
| `/channels/{id}` | DELETE | Remove channel |
| `/channels/{id}/scan` | POST | Trigger scan |
| `/files/{id}/approve` | POST | Approve file |
| `/files/{id}/retry` | POST | Retry failed file |
| `/files/approve/all` | POST | Approve all pending |

## MinIO Buckets

Files are stored in type-specific buckets:
- `audio-storage`: Audio files (mp3, ogg, wav, etc.)
- `pdf-storage`: PDF documents

Access MinIO Console at http://localhost:9001 (default: minioadmin/minioadmin)

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | - | PostgreSQL connection string |
| `TELEGRAM_API_ID` | - | Telegram API ID |
| `TELEGRAM_API_HASH` | - | Telegram API Hash |
| `TELEGRAM_PHONE` | - | Your phone number |
| `MINIO_ENDPOINT` | minio:9000 | MinIO server address |
| `MINIO_ACCESS_KEY` | minioadmin | MinIO access key |
| `MINIO_SECRET_KEY` | minioadmin | MinIO secret key |
| `SCAN_INTERVAL` | 60 | Seconds between scans |
| `LOG_LEVEL` | INFO | Logging level |

## Troubleshooting

### "FloodWaitError" from Telegram

This is normal rate limiting. The scanner automatically waits and retries.

### Files not appearing

1. Check if the channel is added and active
2. Trigger a manual scan from the Channels tab
3. Check logs: `docker-compose logs -f app`

### Container keeps restarting

Check if all environment variables are set:
```bash
docker-compose config
```

### Session issues

If you need to re-authenticate:
1. Stop the app
2. Clear the session: `docker-compose exec postgres psql -U teleminio -c "DELETE FROM telegram_sessions;"`
3. Restart: `docker-compose up -d`

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (needs Postgres and MinIO running)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## License

MIT
