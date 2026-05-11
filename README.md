# WhatsApp Chat Bot Platform

A multi-bot platform for automating WhatsApp conversations with AI support, lead management, and external service integrations.

## Features

- **3 independent WhatsApp bots** with separate sessions and configurations
- **GPT-4o** for processing text, voice, and image messages
- **Semantic product search** via pgvector
- **Integrations**: amoCRM, Telegram notifications, Google Sheets
- **Central admin panel** for managing all bots
- **Human handover**: automatic switching to a live agent
- **Batch message processing** with configurable timeout

## Tech Stack

- **Runtime**: Node.js 20, Python 3.10
- **WhatsApp**: whatsapp-web.js + Puppeteer
- **AI**: OpenAI GPT-4o, Whisper (audio transcription)
- **Database**: PostgreSQL + pgvector (vector search)
- **Infrastructure**: Docker Compose, Nginx
- **Notifications**: Telegram Bot API

## Project Structure
webhook_wb/
├── node-decide/      # Bot 1
├── node-bot2/        # Bot 2
├── node-bot3/        # Bot 3
├── central-admin/    # Admin panel (Python/FastAPI)
├── shared/           # Shared data, prompts, images
├── sessions/         # WhatsApp sessions (do not commit)
├── nginx/            # Reverse proxy config
└── docker-compose.yml

## Installation

### Requirements

- Docker and Docker Compose
- Node.js 20+ (for local development)

### 1. Clone the repository

```bash
git clone https://github.com/Azamat220/Project.git
cd Project
```

### 2. Create a `.env` file

```bash
cp .env.example .env
```

Fill in all variables (see section below).

### 3. Start

```bash
docker-compose up -d
```

Open the admin panel at `http://localhost:8000`.

To connect WhatsApp — scan the QR code in each bot's panel.

## Environment Variables

Create a `.env` file in the project root:

```env
# OpenAI
OPENAI_API_KEY=
OPENAI_API_KEY_2=
OPENAI_API_KEY_3=
OPENAI_ADMIN_KEY=
OPENAI_PROJECT_ID=
OPENAI_PROJECT_ID_2=
OPENAI_PROJECT_ID_3=

# Google Cloud / Sheets
GOOGLE_CREDENTIALS=        # path to gcp.json or JSON string
GOOGLE_SHEET_ID=
GOOGLE_SHEET_NAME=

# Supabase
SUPABASE_URL=
SUPABASE_KEY=

# PostgreSQL
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=

# Telegram
TELEGRAM_BOT_TOKEN_1=
TELEGRAM_CHAT_ID_1=
TELEGRAM_BOT_TOKEN_2=
TELEGRAM_CHAT_ID_2=
TELEGRAM_BOT_TOKEN_3=
TELEGRAM_CHAT_ID_3=

# Bot credentials for admin panel
BOT1_USER=
BOT1_PASS=
BOT2_USER=
BOT2_PASS=
BOT3_USER=
BOT3_PASS=
```

> ⚠️ Never commit `.env` or `shared/secrets/gcp.json` to the repository.

## Prompt Configuration

Prompts for each bot are stored in `shared/bots/<bot_id>/prompt.txt`. They can be updated via API without restarting:

```bash
curl -X POST http://localhost:3001/api/reload-prompt
```

## API Endpoints (per bot)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | WhatsApp connection status |
| GET | `/api/qr` | QR code for authorization |
| GET | `/api/active-chats` | Chats handled by a live agent |
| POST | `/api/send-message` | Send a message |
| POST | `/api/release-chat` | Return chat to the bot |
| POST | `/api/reload-prompt` | Reload prompt |
| POST | `/api/update-settings` | Update timeouts |

## Adding Products to the Catalog

Products are stored in the `products_2` / `products_3` PostgreSQL tables with vector embeddings. To add a new product, insert it into the database with a pre-generated embedding via OpenAI `text-embedding-3-small`.

## Security

- All secrets are passed via environment variables
- `gcp.json` is mounted as a volume, not copied into the image
- Nginx is configured with basic security headers
- Human handover prevents double-processing of chats