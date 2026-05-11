# WhatsApp Chat Bot Platform

Мультибот платформа для автоматизации WhatsApp-переписки с поддержкой ИИ, управления лидами и интеграций с внешними сервисами.

## Возможности

- **3 независимых WhatsApp-бота** с раздельными сессиями и настройками
- **GPT-4o** для обработки текстовых, голосовых и графических сообщений
- **Семантический поиск** по каталогу товаров через pgvector
- **Интеграции**: amoCRM, Telegram-уведомления, Google Sheets
- **Центральная админ-панель** для управления всеми ботами
- **Human handover**: автоматическое переключение на живого менеджера
- **Пакетная обработка** сообщений с настраиваемым таймаутом

## Стек технологий

- **Runtime**: Node.js 20, Python 3.10
- **WhatsApp**: whatsapp-web.js + Puppeteer
- **AI**: OpenAI GPT-4o, Whisper (транскрипция аудио)
- **БД**: PostgreSQL + pgvector (векторный поиск)
- **Инфраструктура**: Docker Compose, Nginx
- **Уведомления**: Telegram Bot API

## Структура проекта# Project

webhook_wb/
├── node-decide/      # Бот 1
├── node-bot2/        # Бот 2
├── node-bot3/        # Бот 3
├── central-admin/    # Панель управления (Python/FastAPI)
├── shared/           # Общие данные, промпты, изображения
├── sessions/         # WhatsApp-сессии (не коммитить)
├── nginx/            # Конфиг реверс-прокси
└── docker-compose.yml


## Установка и запуск

### Требования

- Docker и Docker Compose
- Node.js 20+ (для локальной разработки)

### 1. Клонировать репозиторий

```bash
git clone https://github.com/Azamat220/Project.git
cd Project
```

### 2. Создать файл `.env`

```bash
cp .env.example .env
```

Заполнить все переменные (см. раздел ниже).

### 3. Запустить

```bash
docker-compose up -d
```

Открыть админ-панель: `http://localhost:8000`

Для подключения WhatsApp — отсканировать QR-код в панели каждого бота.

## Переменные окружения

Создайте `.env` файл в корне проекта:

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
GOOGLE_CREDENTIALS=        # путь к gcp.json или JSON-строка
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

# Боты — учётные данные для админки
BOT1_USER=
BOT1_PASS=
BOT2_USER=
BOT2_PASS=
BOT3_USER=
BOT3_PASS=
```

> ⚠️ Никогда не коммитьте `.env` и `shared/secrets/gcp.json` в репозиторий.

## Настройка промптов

Промпты для каждого бота хранятся в `shared/bots/<bot_id>/prompt.txt`. Их можно обновлять через API без перезапуска:

```bash
curl -X POST http://localhost:3001/api/reload-prompt
```

## API эндпоинты (каждый бот)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/status` | Статус подключения WhatsApp |
| GET | `/api/qr` | QR-код для авторизации |
| GET | `/api/active-chats` | Список чатов с живым менеджером |
| POST | `/api/send-message` | Отправить сообщение |
| POST | `/api/release-chat` | Вернуть чат боту |
| POST | `/api/reload-prompt` | Перезагрузить промпт |
| POST | `/api/update-settings` | Изменить таймауты |

## Добавление товаров в каталог

Товары хранятся в таблице `products_2` / `products_3` PostgreSQL с векторными эмбеддингами. Для добавления нового товара нужно записать его в БД с предварительно сгенерированным embedding через OpenAI `text-embedding-3-small`.

## Безопасность

- Все секреты передаются через переменные окружения
- `gcp.json` монтируется как volume, не копируется в образ
- Nginx настроен с базовыми security headers
- Human handover защищает чаты от двойной обработки