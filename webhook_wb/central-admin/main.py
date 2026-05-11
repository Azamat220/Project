import os
import json
import httpx
import asyncpg
from openai import AsyncOpenAI
import re
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, Depends, HTTPException, status, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Query
from pathlib import Path
import json
import asyncio
from jinja2 import Environment
import shutil
from typing import List, Optional, Dict, Any, Union
from enum import Enum
import math
import pandas as pd
import tempfile
import aiofiles
from sqlalchemy import text
import logging
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import time
import requests


class LeadData(BaseModel):
    phone: str
    pipline: Optional[str] = None
    name: Optional[str] = None
    price: Optional[Union[str, int]] = None
    service: Optional[str] = None
    model: Optional[str] = None
    pay_method: Optional[str] = None
    installment_plan: Optional[str] = None
    installment_period: Optional[str] = None
    mileage: Optional[str] = None

#client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Initialize FastAPI app
app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PostgreSQL connection pool
pool = None
subdomain = 'gazongbo'


PIPLINE = {"new_lead": 80840926, "qualification": 80840930, "follow_up": 80840934, "decision_making": 80840938, "installation_booking": 80850154, "installation_in_progress": 80850158}
SERVICE_ENUM = {"filter": 1573859, "gbo": 1573855, "installment": 1573857}
PAY_METHOD_ENUM = {"cash": 1580053, "transfer": 1580057, "installment": 1580055}
INSTALLMENT_PERIOD_ENUM = {"six_month": 1580069, "twelve_month": 1580071}
INSTALLMENT_PLAN_ENUM = {"zero": 1580059, "tumar": 1580061, "m+": 1580063, "asia_bank": 1580065, "ogogo": 1580067}


def clean_dict(d: dict) -> dict:
    """Удаляет ключи со значениями None, пустыми строками и пустыми коллекциями"""
    if not isinstance(d, dict):
        return d
    return {k: clean_dict(v) if isinstance(v, dict) else v
            for k, v in d.items()
            if v not in (None, '', [], {}, ())}


class AmoCRMWrapper:
    def __init__(self, subdomain: str):
        self.subdomain = 'gazongbo'
        self.base_url = f"https://{self.subdomain}.amocrm.ru/api/v4/leads"
        self.headers = {
            "Authorization": f"Bearer {os.getenv('AMO_CRM_ACCESS_TOKEN')}",
            "Content-Type": "application/json"
        }

    def _base_request(self, method: str, data=None, params=None):
            try:
                if method == "get":
                    response = requests.get(self.base_url, headers=self.headers, params=params)
                elif method == "post":
                    response = requests.post(self.base_url, headers=self.headers, json=data)
                elif method == "patch":
                    response = requests.patch(self.base_url, headers=self.headers, json=data)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                response.raise_for_status()
                return response.json()

            except JSONDecodeError:
                print("Ошибка разбора JSON")
            except requests.RequestException as e:
                print(f"Ошибка запроса: {e}")

    # --- Helper ---
    def _filter_payload(self, payload):
        """Удаляет пустые и None значения из структуры перед отправкой"""
        if isinstance(payload, list):
            return [self._filter_payload(p) for p in payload if p]
        elif isinstance(payload, dict):
            return {k: self._filter_payload(v)
                    for k, v in payload.items()
                    if v not in (None, '', [], {}, ())}
        return payload

    def _send_form(self, name, lead_name, custom_fields, price=None, pipline_id=None):
        # Базовая структура лида
        data = [{
            "name": name or lead_name,
            "price": price,
            "pipeline_id": 10210954,
            "status_id": pipline_id,
            "custom_fields_values": [f for f in custom_fields if f]
        }]
        clean_data = self._filter_payload(data)
        print("Отправляемые данные:", clean_data)  # Для отладки
        return self._base_request("post", data=clean_data)

    def _update_lead(self, lead_id, name=None, lead_name=None, custom_fields=None,
                     price=None, pipline_id=None):
        data = [{
            "id": lead_id,
            "name": name or lead_name,
            "price": price,
            "pipeline_id": 10210954,
            "status_id": pipline_id,
            "custom_fields_values": [f for f in (custom_fields or []) if f],
        }]
        clean_data = self._filter_payload(data)
        print("Обновление данных:", clean_data)  # Для отладки
        return self._base_request("patch", data=clean_data)


@app.post("/send_amocrm_lead")
async def send_amocrm_lead(data: LeadData):
    change_status = None
    lead_id = None

    try:
        async with pool.acquire() as conn:
            existing_client = await conn.fetchrow(
                "SELECT id_amo_crm, pipline FROM client_leads WHERE phone = $1",
                data.phone
            )

            if existing_client:
                lead_id = existing_client["id_amo_crm"]
                if existing_client["pipline"] != data.pipline:
                    change_status = 'updated'
                    await conn.execute(
                        "UPDATE client_leads SET pipline = $1 WHERE phone = $2",
                        data.pipline, data.phone
                    )
            else:
                change_status = 'created'
                await conn.execute(
                    "INSERT INTO client_leads (phone, pipline) VALUES ($1, $2)",
                    data.phone, data.pipline
                )

    except Exception as e:
        print(f"Error updating client: {str(e)}")

    amo = AmoCRMWrapper(subdomain="gazongbo")

    custom_fields = [
        {"field_id": 1956463, "values": [{"value": data.phone}]},  # телефон — всегда обязателен
        {"field_id": 1956465, "values": [{"value": data.name}]} if data.name else None,
        {"field_id": 1935587, "values": [{"enum_id": SERVICE_ENUM.get(data.service)}]} if data.service else None,
        {"field_id": 1935589, "values": [{"value": data.model}]} if data.model else None,
        {"field_id": 1944151, "values": [{"enum_id": PAY_METHOD_ENUM.get(data.pay_method)}]} if data.pay_method else None,
        {"field_id": 1944153, "values": [{"enum_id": INSTALLMENT_PLAN_ENUM.get(data.installment_plan)}]} if data.installment_plan else None,
        {"field_id": 1944155, "values": [{"enum_id": INSTALLMENT_PERIOD_ENUM.get(data.installment_period)}]} if data.installment_period else None,
        {"field_id": 1935599, "values": [{"value": data.mileage}]} if data.mileage else None,
]

    # Убираем None из списка
    custom_fields = [field for field in custom_fields if field]

    # Создание или обновление
    if not lead_id:
        response = amo._send_form(
            name=data.name,
            lead_name="Лид от бота",
            custom_fields=custom_fields,
            price=int(data.price) if data.price else 0,
            pipline_id=PIPLINE.get(data.pipline)
        )

        if response and "_embedded" in response and "leads" in response["_embedded"]:
            leads = response["_embedded"]["leads"]
            if isinstance(leads, list) and len(leads) > 0:
                new_lead_id = leads[0].get("id")
                if new_lead_id:
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE client_leads SET id_amo_crm = $1 WHERE phone = $2",
                            str(new_lead_id), data.phone
                        )
    elif not change_status:
        pass

    else:
        amo._update_lead(
            lead_id=lead_id,
            name=data.name,
            lead_name="Лид от бота",
            custom_fields=custom_fields,
            price=int(data.price) if data.price else 0,
            pipline_id=PIPLINE.get(data.pipline)
        )
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE client_leads SET pipline = $1 WHERE phone = $2",
                data.pipline, data.phone
            )

    return JSONResponse({"status": 200})


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



async def create_db_pool():
    global pool
    pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        user=os.getenv("POSTGRES_USER", "adminbek"),
        password=os.getenv("POSTGRES_PASSWORD", "passwordbek"),
        database=os.getenv("POSTGRES_DB", "moidb")
    )

async def initialize_database():
    conn = await pool.acquire()
    try:
        # Create clients table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                name TEXT,
                phone TEXT,
                last_message TEXT,
                last_contact TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                temperature TEXT DEFAULT 'холодный',
                manager_status TEXT DEFAULT 'Зелёный',
                manager_comment TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                temperature TEXT DEFAULT 'холодный',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, bot_id)
            )
        ''')
        
        # Create mailing campaigns table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS mailing_campaigns (
                id SERIAL PRIMARY KEY,
                bot_id TEXT NOT NULL,
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                excel_file_path TEXT,
                total_contacts INTEGER DEFAULT 0,
                sent_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'draft',
                delay_seconds INTEGER DEFAULT 2,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ
            )
        ''')
        
        # Create mailing contacts table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS mailing_contacts (
                id SERIAL PRIMARY KEY,
                campaign_id INTEGER REFERENCES mailing_campaigns(id) ON DELETE CASCADE,
                phone_number TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                sent_at TIMESTAMPTZ,
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_summaries_user_bot ON conversation_summaries (user_id, bot_id);
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_mailing_campaigns_bot ON mailing_campaigns (bot_id);
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_mailing_contacts_campaign ON mailing_contacts (campaign_id);
        ''')

        await conn.execute('''
            CREATE TABLE IF NOT EXISTS client_leads (
                id SERIAL PRIMARY KEY,
                phone TEXT UNIQUE NOT NULL,
                pipline TEXT,
                id_amo_crm TEXT)
        ''')

        print('Database initialized successfully')
    except Exception as e:
        print(f'Database initialization error: {e}')
    finally:
        await pool.release(conn)

@app.on_event("startup")
async def startup_event():
    await create_db_pool()
    await initialize_database()

def intcomma(value):
    """Format numbers with commas as thousands separators."""
    try:
        num = float(value)
        return "{:,.0f}".format(num)
    except (ValueError, TypeError):
        return str(value)

templates = Jinja2Templates(directory="templates")
templates.env.filters["intcomma"] = intcomma
Path("templates").mkdir(exist_ok=True)

# Create static directory for uploaded files
Path("static").mkdir(exist_ok=True)
Path("static/uploads").mkdir(exist_ok=True)
Path("static/excel").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

def read_bot_config(bot_id):
    config_file = f"/data/bots/{bot_id}/config.json"
    default_config = {
        "batch_timeout_seconds": 5,
        "auto_release_minutes": 30
    }

    try:
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                return json.load(f)
    except Exception:
        pass

    return default_config

def write_bot_config(bot_id, config):
    config_file = f"/data/bots/{bot_id}/config.json"
    try:
        Path(config_file).parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as f:
            json.dump(config, f)
        return True
    except Exception:
        return False

# Bot configuration
BOT_CONFIG = {
    "bot1": {
        "api_url": "http://bot1:3001/api",
        "prompt_file": "/data/bots/bot1/prompt.txt",
        "username": os.getenv("BOT1_USER", "admin1"),
        "password": os.getenv("BOT1_PASS", "password1"),
        "table_name": os.getenv("BOT1_TABLE", "products"),
        "openai_admin_key": os.getenv("OPENAI_ADMIN_KEY"),
        "project_id": os.getenv("OPENAI_PROJECT_ID"),
        "client": AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), project=os.getenv("OPENAI_PROJECT_ID")),
        "conversation": "conversations"
    },
    "bot2": {
        "api_url": "http://bot2:3002/api",
        "prompt_file": "/data/bots/bot2/prompt.txt",
        "username": os.getenv("BOT2_USER", "admin2"),
        "password": os.getenv("BOT2_PASS", "password2"),
        "table_name": os.getenv("BOT2_TABLE", "products_2"),
        "openai_admin_key": os.getenv("OPENAI_ADMIN_KEY"),
        "project_id": os.getenv("OPENAI_PROJECT_ID_2"),
        "client": AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY_2"), project=os.getenv("OPENAI_PROJECT_ID_2")),
        "conversation": "conversations_2"
    },
     "bot3": {
         "api_url": "http://bot3:3003/api",
         "prompt_file": "/data/bots/bot3/prompt.txt",
         "username": os.getenv("BOT3_USER", "admin3"),
         "password": os.getenv("BOT3_PASS", "password3"),
         "table_name": os.getenv("BOT3_TABLE", "products_3"),
         "openai_admin_key": os.getenv("OPENAI_ADMIN_KEY"),
         "project_id": os.getenv("OPENAI_PROJECT_ID_3"),
         "client": AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY_3"), project=os.getenv("OPENAI_PROJECT_ID_3")),
         "conversation": "conversations_3"
     }
}

# Create prompt files if they don't exist
for bot_id, config in BOT_CONFIG.items():
    Path(config['prompt_file']).parent.mkdir(parents=True, exist_ok=True)

    if not Path(config['prompt_file']).exists():
        with open(config['prompt_file'], "w") as f:
            f.write("You are a helpful assistant. Respond professionally to user queries.")

# Helper functions
def verify_bot_credentials(bot_id: str, credentials: HTTPBasicCredentials):
    if bot_id not in BOT_CONFIG:
        return False

    bot_config = BOT_CONFIG[bot_id]
    correct_username = credentials.username == bot_config.get("username", "")
    correct_password = credentials.password == bot_config.get("password", "")

    return correct_username and correct_password

async def get_bot_status(bot_id, config):
    try:
        async with httpx.AsyncClient() as client:
            status_response = await client.get(f"{config['api_url']}/status")
            qr_response = await client.get(f"{config['api_url']}/qr")
            active_chats_response = await client.get(f"{config['api_url']}/active-chats")

            # Получаем JSON один раз
            status_data = status_response.json()
            
            # Пытаемся найти номер телефона в разных возможных полях
            phone = status_data.get('phone') or status_data.get('number') or status_data.get('wid')

            return {
                'ready': status_response.json().get('ready', False),
                'phone': phone, # <--- ДОБАВЛЕНО: Возвращаем номер
                'qr': qr_response.json().get('qr', ''),
                'active_chats': active_chats_response.json().get('chats', []) if active_chats_response.status_code == 200 else [],
                'error': None
            }
    except Exception as e:
        return {
            'ready': False,
            'phone': None, # <--- ДОБАВЛЕНО
            'qr': '',
            'active_chats': [],
            'error': str(e)
        }

async def get_bot_stats(bot_id):
    conversation = BOT_CONFIG[bot_id]['conversation']
    try:
        async with pool.acquire() as conn:
            # Build the SQL queries manually with table name
            user_messages_query = "SELECT COUNT(*) FROM {} WHERE role = 'user'".format(conversation)
            active_users_query = "SELECT COUNT(DISTINCT user_id) FROM {}".format(conversation)

            # Execute the queries (no parameters needed)
            user_messages = await conn.fetchval(user_messages_query)
            active_users = await conn.fetchval(active_users_query)

        print(user_messages, active_users, 'LOG TO STATS')
        return user_messages, active_users

    except Exception as e:
        print(f"Error in get_bot_stats: {e}")
        return 0, 0

def get_bot_prompt(prompt_file):
    try:
        with open(prompt_file, "r") as f:
            return f.read()
    except Exception:
        return ""

def save_bot_prompt(prompt_file, prompt):
    try:
        with open(prompt_file, "w") as f:
            f.write(prompt)
        return True
    except Exception:
        return False

async def get_user_conversations(bot_id: str, user_id: str, limit: int = 20) -> str:
    """Retrieve conversation history for a user"""
    table_name = BOT_CONFIG[bot_id]["conversation"]

    try:
        async with pool.acquire() as conn:
            # Manual string concatenation (not f-string), only for table name
            query = (
                "SELECT role, message, timestamp "
                "FROM " + table_name + " "
                "WHERE user_id = $1 "
                "ORDER BY timestamp DESC "
                "LIMIT $2"
            )

            rows = await conn.fetch(query, user_id, limit)

            conversation = []
            for row in rows:
                prefix = "Клиент" if row["role"] == "user" else "Агент"
                conversation.append(f"[{row['timestamp']}] {prefix}: {row['message']}")

            return "\n".join(conversation)

    except Exception as e:
        print(f"Ошибка базы данных: {str(e)}")
        return ""

async def generate_conversation_summary_and_temperature(bot_id: str, user_id: str) -> tuple[str, str]:
    """Generate a summary of the conversation and determine temperature using GPT"""
    try:
        # Get conversation history
        conversation = await get_user_conversations(bot_id, user_id, limit=20)

        if not conversation:
            return "Нет данных для анализа", "холодный"

        # Prepare prompt for GPT to analyze both summary and temperature
        prompt = f"""
        Проанализируйте следующий диалог между клиентом и агентом и выполните две задачи:

        1. Создайте краткое содержание (2-3 предложения) на русском языке.
        2. Определите температуру клиента (горячий, теплый, холодный) по следующим критериям:

        Критерии температуры:
        - ГОРЯЧИЙ: клиент проявляет высокий интерес, задает конкретные вопросы о покупке,
          обсуждает сроки, цены, доставку, проявляет срочность, готов к действию.
        - ТЕПЛЫЙ: клиент интересуется продуктом, задает уточняющие вопросы,
          но еще не готов к покупке, изучает варианты.
        - ХОЛОДНЫЙ: клиент только знакомится с информацией, задает общие вопросы,
          не проявляет конкретных намерений к покупке.

        Разговор:
        {conversation}

        Ответ предоставьте в формате JSON:
        {{
            "summary": "краткое содержание здесь",
            "temperature": "горячий|теплый|холодный"
        }}
        """

        # Call OpenAI API
        response = await BOT_CONFIG[bot_id]["client"].chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON response
        try:
            # Extract JSON from response (in case there's additional text)
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                summary = data.get("summary", "Ошибка генерации содержания")
                temperature = data.get("temperature", "холодный")

                # Validate temperature
                if temperature not in ["горячий", "теплый", "холодный"]:
                    temperature = "холодный"

                return summary, temperature
            else:
                return content, "холодный"
        except json.JSONDecodeError:
            # If JSON parsing fails, use the whole response as summary
            return content, "холодный"

    except Exception as e:
        print(f"Error generating conversation summary and temperature: {str(e)}")
        return "Ошибка при генерации содержания", "холодный"

async def get_products(bot_id: str) -> List[dict]:
    """Get all products from database"""
    try:
        table_name = BOT_CONFIG[bot_id]['table_name']
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT id, name, description, price, image FROM {table_name} ORDER BY created_at DESC"
            )

            products = []
            for row in rows:
                products.append({
                    "id": row['id'],
                    "name": row['name'],
                    "description": row['description'],
                    "price": row['price'],
                    "image": row['image']
                })

            return products
    except Exception as e:
        print(f"Error getting products: {str(e)}")
        return []

async def get_embedding(text: str, bot_id: str) -> list[float]:
    """Generate an embedding for the given text using OpenAI's API."""
    print(bot_id, 'get embedd')
    response = await BOT_CONFIG[bot_id]['client'].embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

async def add_product(name: str, description: str, price: str, image: str = None, bot_id: str = None) -> bool:
    """Add a new product to database with GPT embeddings"""
    table_name = BOT_CONFIG[bot_id]['table_name']
    try:
        # Generate embedding
        combined_text = f"Name: {name}; Description: {description}"
        embedding = await get_embedding(combined_text, bot_id)  # Returns list[float]

        # Convert embedding list to PostgreSQL vector string format
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        async with pool.acquire() as conn:
            await conn.execute(
                f"""INSERT INTO {table_name} (name, description, price, image, embedding)
                VALUES ($1, $2, $3, $4, $5::vector(1536))""",
                name, description, price, image, embedding_str
            )
            return True
    except Exception as e:
        print(f"Error adding product: {str(e)}")
        return False

async def delete_product(product_id: int, bot_id: str) -> bool:
    """Delete a product from database"""
    table_name = BOT_CONFIG[bot_id]['table_name']
    try:
        async with pool.acquire() as conn:
            # Get image path to delete the file
            image_path = await conn.fetchval(
                f"SELECT image FROM {table_name} WHERE id = $1",
                product_id
            )

            if image_path and os.path.exists(image_path):
                os.remove(image_path)

            await conn.execute(
                f"DELETE FROM {table_name} WHERE id = $1",
                product_id
            )
            return True
    except Exception as e:
        print(f"Error deleting product: {str(e)}")
        return False

async def get_clients(bot_id: str, temperature_filter: str = None, manager_status_filter: str = None, page: int = 1, page_size: int = 20) -> dict:
    """Get paginated clients with their conversation summaries and GPT-determined temperatures"""
    conversation = BOT_CONFIG[bot_id]["conversation"]
    offset = (page - 1) * page_size

    try:
        async with pool.acquire() as conn:
            # Build base query for counting total clients
            count_query = f"""
                SELECT COUNT(DISTINCT user_id)
                FROM {conversation}
                WHERE role = 'user'
            """

            # Build main query for paginated clients
            query = f"""
                SELECT DISTINCT ON (user_id)
                    user_id,
                    MAX(timestamp) as last_contact
                FROM {conversation}
                WHERE role = 'user'
                GROUP BY user_id
                ORDER BY user_id, last_contact DESC
                LIMIT $1 OFFSET $2
            """

            # Get total count
            total_clients = await conn.fetchval(count_query)
            total_pages = math.ceil(total_clients / page_size) if total_clients > 0 else 1

            # Get paginated user IDs
            rows = await conn.fetch(query, page_size, offset)

            clients = []
            for row in rows:
                user_id = row['user_id']
                last_contact = row['last_contact']

                # Get last message
                last_message_row = await conn.fetchrow(
                    f"""
                    SELECT message FROM {conversation}
                    WHERE user_id = $1 AND role = 'user'
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    user_id
                )

                last_message = last_message_row['message'] if last_message_row else ""

                # Extract phone number from user_id
                phone = user_id.split('@')[0] if '@' in user_id else user_id

                # Get client info from clients table
                client_info = await conn.fetchrow(
                    "SELECT name, manager_status, manager_comment FROM clients WHERE user_id = $1 AND bot_id = $2",
                    user_id, bot_id
                )

                if client_info:
                    name = client_info['name']
                    manager_status = client_info['manager_status'] or 'Зелёный'
                    manager_comment = client_info['manager_comment'] or ''
                else:
                    name = ""
                    manager_status = 'Зелёный'
                    manager_comment = ''
                    # Insert default client record if doesn't exist
                    await conn.execute(
                        "INSERT INTO clients (user_id, bot_id, name, phone, temperature, manager_status) VALUES ($1, $2, $3, $4, $5, $6)",
                        user_id, bot_id, name, phone, 'холодный', manager_status
                    )

                # Check if we need to generate/update the summary and temperature
                summary_needed = await check_if_summary_needs_update(conn, user_id, bot_id, last_contact)

                if summary_needed:
                    # Generate new summary and temperature using GPT
                    summary, temperature = await generate_conversation_summary_and_temperature(bot_id, user_id)

                    # Store the summary in the conversation_summaries table
                    await conn.execute(
                        """
                        INSERT INTO conversation_summaries (user_id, bot_id, summary, temperature)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (user_id, bot_id)
                        DO UPDATE SET summary = $3, temperature = $4, updated_at = NOW()
                        """,
                        user_id, bot_id, summary, temperature
                    )

                    # Update temperature in clients table
                    await conn.execute(
                        "UPDATE clients SET temperature = $1 WHERE user_id = $2 AND bot_id = $3",
                        temperature, user_id, bot_id
                    )
                else:
                    # Get existing summary and temperature
                    summary_row = await conn.fetchrow(
                        "SELECT summary, temperature FROM conversation_summaries WHERE user_id = $1 AND bot_id = $2",
                        user_id, bot_id
                    )
                    if summary_row:
                        summary = summary_row['summary']
                        temperature = summary_row['temperature'] or 'холодный'
                    else:
                        summary = "No summary available"
                        temperature = 'холодный'

                # Apply temperature filter if specified
                if temperature_filter and temperature != temperature_filter:
                    continue

                # Apply manager status filter if specified
                if manager_status_filter and manager_status != manager_status_filter:
                    continue

                clients.append({
                    "user_id": user_id,
                    "name": name,
                    "phone": phone,
                    "last_contact": last_contact,
                    "last_message": last_message[:100] + "..." if len(last_message) > 100 else last_message,
                    "summary": summary,
                    "temperature": temperature,
                    "manager_status": manager_status,
                    "manager_comment": manager_comment
                })

            # Apply filters after fetching
            if temperature_filter:
                clients = [client for client in clients if client['temperature'] == temperature_filter]
            if manager_status_filter:
                clients = [client for client in clients if client['manager_status'] == manager_status_filter]

            # Recalculate total pages based on filtered results
            total_clients = len(clients)
            total_pages = math.ceil(total_clients / page_size) if total_clients > 0 else 1

            return {
                "clients": clients,
                "total_clients": total_clients,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": page_size,
                "has_prev": page > 1,
                "has_next": page < total_pages
            }
    except Exception as e:
        print(f"Error getting clients: {str(e)}")
        return {
            "clients": [],
            "total_clients": 0,
            "total_pages": 1,
            "current_page": page,
            "page_size": page_size,
            "has_prev": False,
            "has_next": False
        }

async def check_if_summary_needs_update(conn, user_id: str, bot_id: str, last_contact: datetime) -> bool:
    """Check if a summary needs to be generated or updated"""
    # Check if summary exists
    summary_row = await conn.fetchrow(
        "SELECT summary, updated_at FROM conversation_summaries WHERE user_id = $1 AND bot_id = $2",
        user_id, bot_id
    )

    if not summary_row:
        return True  # No summary exists, need to generate one

    # Convert both to UTC timestamps for comparison (avoids timezone issues)
    if last_contact:
        last_contact_utc = last_contact.replace(tzinfo=timezone.utc) if last_contact.tzinfo is None else last_contact.astimezone(timezone.utc)
    else:
        last_contact_utc = datetime.now(timezone.utc)

    updated_at = summary_row['updated_at']
    updated_at_utc = updated_at.replace(tzinfo=timezone.utc) if updated_at.tzinfo is None else updated_at.astimezone(timezone.utc)

    # Check if the conversation has been updated since the last summary
    if last_contact_utc > updated_at_utc:
        return True  # Conversation updated, need to update summary

    # Also update if summary was generated more than 24 hours ago
    current_time_utc = datetime.now(timezone.utc)
    if current_time_utc - updated_at_utc > timedelta(hours=24):
        return True

    return False  # Summary is current, no need to update

async def update_client(bot_id: str, user_id: str, name: str = "", phone: str = "", manager_status: str = None, manager_comment: str = None) -> bool:
    """Update client information including manager status and comments"""
    try:
        async with pool.acquire() as conn:
            # Check if client already exists
            existing_client = await conn.fetchrow(
                "SELECT id, name, phone, manager_status, manager_comment FROM clients WHERE user_id = $1 AND bot_id = $2",
                user_id, bot_id
            )

            if existing_client:
                # Use existing values if not provided
                update_name = name if name is not None else existing_client['name']
                update_phone = phone if phone is not None else existing_client['phone']
                update_manager_status = manager_status if manager_status is not None else existing_client['manager_status']
                update_manager_comment = manager_comment if manager_comment is not None else existing_client['manager_comment']

                await conn.execute(
                    "UPDATE clients SET name = $1, phone = $2, manager_status = $3, manager_comment = $4, last_contact = NOW() WHERE user_id = $5 AND bot_id = $6",
                    update_name, update_phone, update_manager_status, update_manager_comment, user_id, bot_id
                )
            else:
                # Get temperature from conversation_summaries or default to 'холодный'
                temp_row = await conn.fetchrow(
                    "SELECT temperature FROM conversation_summaries WHERE user_id = $1 AND bot_id = $2",
                    user_id, bot_id
                )
                temperature = temp_row['temperature'] if temp_row else 'холодный'

                await conn.execute(
                    "INSERT INTO clients (user_id, bot_id, name, phone, temperature, manager_status, manager_comment) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    user_id, bot_id, name or "", phone or "", temperature, manager_status or 'Зелёный', manager_comment or ''
                )

            return True
    except Exception as e:
        print(f"Error updating client: {str(e)}")
        return False

# Mailing functionality
async def process_excel_file(file_path: str) -> List[str]:
    """Process Excel file and extract phone numbers"""
    try:
        # Чтение Excel файла без заголовков
        df = pd.read_excel(file_path, header=None)
        
        phone_numbers = []

        # Проверяем столбцы на наличие телефонных номеров
        for col in df.columns:
            sample_data = df[col].dropna().head(10)
            print(f"Sample data from column {col}:\n{sample_data}\n")
            
            # Проверяем, есть ли телефонный номер в первых 10 значениях
            if sample_data.astype(str).str.contains(r'\+?\d{7,15}', regex=True).any():
                phones = df[col].dropna().astype(str).tolist()
                phone_numbers.extend(phones)

        # Если телефоны не найдены в столбцах, ищем их в заголовках
        for col in df.columns:
            if re.search(r'\+?\d{7,15}', str(col)):
                phone_numbers.append(str(col))

        # Очищаем и форматируем номера телефонов
        cleaned_numbers = []
        for phone in phone_numbers:
            cleaned = re.sub(r'\D', '', phone)
            if 7 <= len(cleaned) <= 15:
                if not phone.startswith('+'):
                    cleaned = '+' + cleaned
                cleaned_numbers.append(cleaned)

        # Если номера не найдены, логируем предупреждение
        if not cleaned_numbers:
            logger.warning(f"No phone numbers found in Excel file: {file_path}")

        return cleaned_numbers

    except Exception as e:
        logger.error(f"Error processing Excel file: {str(e)}")
        return []


async def create_mailing_campaign(bot_id: str, name: str, message: str, excel_file_path: str = None, delay_seconds: int = 2) -> int:
    """Create a new mailing campaign"""
    try:
        async with pool.acquire() as conn:
            # Create campaign
            campaign_id = await conn.fetchval(
                '''
                INSERT INTO mailing_campaigns (bot_id, name, message, excel_file_path, delay_seconds, status)
                VALUES ($1, $2, $3, $4, $5, 'draft')
                RETURNING id
                ''',
                bot_id, name, message, excel_file_path, delay_seconds
            )
            
            # If Excel file provided, process it and add contacts
            if excel_file_path and os.path.exists(excel_file_path):
                phone_numbers = await process_excel_file(excel_file_path)
                
                # Add contacts to campaign only if we found phone numbers
                if phone_numbers:
                    for phone in phone_numbers:
                        await conn.execute(
                            '''
                            INSERT INTO mailing_contacts (campaign_id, phone_number, status)
                            VALUES ($1, $2, 'pending')
                            ''',
                            campaign_id, phone
                        )
                    
                    # Update total contacts count
                    await conn.execute(
                        '''
                        UPDATE mailing_campaigns 
                        SET total_contacts = $1 
                        WHERE id = $2
                        ''',
                        len(phone_numbers), campaign_id
                    )
                else:
                    logger.warning(f"No phone numbers found in Excel file: {excel_file_path}")
                    # You might want to set status to failed or draft depending on your needs
            
            return campaign_id
    except Exception as e:
        logger.error(f"Error creating mailing campaign: {str(e)}")
        raise

async def get_mailing_campaigns(bot_id: str) -> List[Dict]:
    """Get all mailing campaigns for a bot"""
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT 
                    id, name, message, excel_file_path, total_contacts, 
                    sent_count, failed_count, status, delay_seconds,
                    created_at, started_at, completed_at
                FROM mailing_campaigns 
                WHERE bot_id = $1 
                ORDER BY created_at DESC
            ''', bot_id)
            
            campaigns = []
            for row in rows:
                campaigns.append(dict(row))
            
            return campaigns
    except Exception as e:
        logger.error(f"Error getting mailing campaigns: {str(e)}")
        return []

from fastapi.encoders import jsonable_encoder

async def get_campaign_details(campaign_id: int) -> Dict:
    """Get detailed information about a campaign"""
    try:
        async with pool.acquire() as conn:
            # Get campaign info
            campaign = await conn.fetchrow(
                'SELECT * FROM mailing_campaigns WHERE id = $1',
                campaign_id
            )
            
            if not campaign:
                return None
            
            # Get contacts statistics
            stats = await conn.fetchrow(
                '''
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN status = 'sent' THEN 1 END) as sent,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending
                FROM mailing_contacts 
                WHERE campaign_id = $1
                ''',
                campaign_id
            )
            
            # Convert asyncpg record to dict and handle datetime serialization
            campaign_dict = dict(campaign)
            stats_dict = dict(stats) if stats else None
            
            # Convert datetime objects to ISO format strings for JSON serialization
            datetime_fields = ['created_at', 'started_at', 'completed_at']
            for field in datetime_fields:
                if field in campaign_dict and campaign_dict[field] is not None:
                    campaign_dict[field] = campaign_dict[field].isoformat()
            
            return {
                "campaign": campaign_dict,
                "stats": stats_dict
            }
    except Exception as e:
        logger.error(f"Error getting campaign details: {str(e)}")
        return None

@app.get("/campaign-details/{campaign_id}")
async def get_campaign_details_handler(
    request: Request,
    campaign_id: int
):
    username = request.cookies.get("username")
    bot_id = request.cookies.get("bot_id")

    if not username or not bot_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    details = await get_campaign_details(campaign_id)
    if not details:
        return JSONResponse({"error": "Campaign not found"}, status_code=404)

    # Use FastAPI's jsonable_encoder to handle serialization
    return JSONResponse(jsonable_encoder(details))



async def send_whatsapp_message(bot_id: str, phone_number: str, message: str) -> bool:
    """Send WhatsApp message using bot API"""
    try:
        config = BOT_CONFIG[bot_id]
        chat_id = f"{phone_number}@c.us"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config['api_url']}/send-message",
                json={
                    "chat_id": chat_id,
                    "message": message
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Failed to send message: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {str(e)}")
        return False

async def process_mailing_campaign(campaign_id: int):
    """Process mailing campaign in background"""
    try:
        async with pool.acquire() as conn:
            # Get campaign details
            campaign = await conn.fetchrow(
                'SELECT * FROM mailing_campaigns WHERE id = $1',
                campaign_id
            )
            
            if not campaign:
                logger.error(f"Campaign {campaign_id} not found")
                return
            
            bot_id = campaign['bot_id']
            message = campaign['message']
            delay_seconds = campaign['delay_seconds']
            
            # Check if there are any contacts to process
            contacts_count = await conn.fetchval(
                'SELECT COUNT(*) FROM mailing_contacts WHERE campaign_id = $1',
                campaign_id
            )
            
            if contacts_count == 0:
                logger.warning(f"Campaign {campaign_id} has no contacts to process")
                await conn.execute(
                    '''
                    UPDATE mailing_campaigns 
                    SET status = 'completed', completed_at = NOW()
                    WHERE id = $1
                    ''',
                    campaign_id
                )
                return
            
            # Update campaign status to running
            await conn.execute(
                '''
                UPDATE mailing_campaigns 
                SET status = 'running', started_at = NOW() 
                WHERE id = $1
                ''',
                campaign_id
            )
            
            # Get pending contacts
            contacts = await conn.fetch('''
                SELECT id, phone_number 
                FROM mailing_contacts 
                WHERE campaign_id = $1 AND status = 'pending'
                ORDER BY id
            ''', campaign_id)
            
            sent_count = 0
            failed_count = 0
            
            # Process each contact
            for contact in contacts:
                contact_id = contact['id']
                phone_number = contact['phone_number']
                
                try:
                    # Send message
                    success = await send_whatsapp_message(bot_id, phone_number, message)
                    
                    if success:
                        await conn.execute('''
                            UPDATE mailing_contacts 
                            SET status = 'sent', sent_at = NOW() 
                            WHERE id = $1
                        ''', contact_id)
                        sent_count += 1
                    else:
                        await conn.execute('''
                            UPDATE mailing_contacts 
                            SET status = 'failed', error_message = 'Failed to send message'
                            WHERE id = $1
                        ''', contact_id)
                        failed_count += 1
                    
                    # Update campaign progress
                    await conn.execute('''
                        UPDATE mailing_campaigns 
                        SET sent_count = sent_count + $1, failed_count = failed_count + $2
                        WHERE id = $3
                    ''', 1 if success else 0, 0 if success else 1, campaign_id)
                    
                    # Delay between messages to avoid being banned
                    if delay_seconds > 0:
                        await asyncio.sleep(delay_seconds)
                        
                except Exception as e:
                    logger.error(f"Error processing contact {contact_id}: {str(e)}")
                    await conn.execute('''
                        UPDATE mailing_contacts 
                        SET status = 'failed', error_message = $1
                        WHERE id = $2
                    ''', str(e), contact_id)
                    failed_count += 1
                    
                    # Update campaign failed count
                    await conn.execute('''
                        UPDATE mailing_campaigns 
                        SET failed_count = failed_count + 1
                        WHERE id = $1
                    ''', campaign_id)
            
            # Update campaign status to completed
            await conn.execute('''
                UPDATE mailing_campaigns 
                SET status = 'completed', completed_at = NOW()
                WHERE id = $1
            ''', campaign_id)
            
            logger.info(f"Campaign {campaign_id} completed: {sent_count} sent, {failed_count} failed")
            
    except Exception as e:
        logger.error(f"Error processing campaign {campaign_id}: {str(e)}")
        
        # Update campaign status to failed
        try:
            async with pool.acquire() as conn:
                await conn.execute('''
                    UPDATE mailing_campaigns 
                    SET status = 'failed'
                    WHERE id = $1
                ''', campaign_id)
        except Exception as update_error:
            logger.error(f"Error updating campaign status: {update_error}")

# Routes
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    for bot_id, config in BOT_CONFIG.items():
        if username == config["username"] and password == config["password"]:
            response = RedirectResponse(url=f"/manage/{bot_id}", status_code=303)
            response.set_cookie(key="bot_id", value=bot_id)
            response.set_cookie(key="username", value=username)
            return response

    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid credentials"
    })

@app.get("/generate-summary/{bot_id}/{user_id}")
async def generate_summary_endpoint(
        request: Request,
        bot_id: str,
        user_id: str
):
    # Check authentication
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if bot_id not in BOT_CONFIG:
        return JSONResponse({"error": "Bot not found"}, status_code=404)

    # Generate summary and temperature
    summary, temperature = await generate_conversation_summary_and_temperature(bot_id, user_id)

    # Store the summary and temperature in the database
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_summaries (user_id, bot_id, summary, temperature)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, bot_id)
                DO UPDATE SET summary = $3, temperature = $4, updated_at = NOW()
                """,
                user_id, bot_id, summary, temperature
            )

            # Also update temperature in clients table
            await conn.execute(
                "UPDATE clients SET temperature = $1 WHERE user_id = $2 AND bot_id = $3",
                temperature, user_id, bot_id
            )
    except Exception as e:
        print(f"Error saving summary to database: {str(e)}")

    return JSONResponse({"summary": summary, "temperature": temperature})

@app.get("/manage/{bot_id}", response_class=HTMLResponse)
async def bot_management(
        request: Request,
        bot_id: str,
        success: bool = False,
        error: str = None
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    config = BOT_CONFIG[bot_id]
    bot_status = await get_bot_status(bot_id, config)
    user_messages, active_users = await get_bot_stats(bot_id)
    current_prompt = get_bot_prompt(config['prompt_file'])
    bot_config = read_bot_config(bot_id)
    products = await get_products(bot_id)

    return templates.TemplateResponse("bot_management.html", {
        "request": request,
        "bot_id": bot_id,
        "bot_name": f"Bot {bot_id}",
        "ready": bot_status['ready'],
        "phone": bot_status.get('phone'), # <--- ДОБАВЛЕНО: Передаем номер в HTML
        "qr": bot_status['qr'],
        "active_chats": bot_status['active_chats'],
        "error": bot_status['error'],
        "user_messages": user_messages,
        "active_users": active_users,
        "current_prompt": current_prompt,
        "prompt_length": len(current_prompt),
        "success": success,
        "error_msg": error,
        "batch_timeout": bot_config.get("batch_timeout_seconds", 5),
        "auto_release": bot_config.get("auto_release_minutes", 30),
        "products": products
    })

@app.post("/update-settings/{bot_id}")
async def update_settings(
        request: Request,
        bot_id: str,
        batch_timeout: int = Form(...),
        auto_release: int = Form(...)
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    config = {
        "batch_timeout_seconds": batch_timeout,
        "auto_release_minutes": auto_release
    }

    if not write_bot_config(bot_id, config):
        return RedirectResponse(
            f"/manage/{bot_id}?error=Error+saving+settings",
            status_code=303
        )

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{BOT_CONFIG[bot_id]['api_url']}/update-settings",
                json=config
            )
    except Exception as e:
        return RedirectResponse(
            f"/manage/{bot_id}?error=Error+notifying+bot: {str(e)}",
            status_code=303
        )

    return RedirectResponse(f"/manage/{bot_id}?success=true", status_code=303)

@app.post("/update-prompt/{bot_id}")
async def update_prompt(
        request: Request,
        bot_id: str,
        prompt: str = Form(...)
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    config = BOT_CONFIG[bot_id]

    if len(prompt) < 100:
        return RedirectResponse(
            f"/manage/{bot_id}?error=Prompt+must+be+at+least+100+characters",
            status_code=303
        )

    if save_bot_prompt(config['prompt_file'], prompt):
        try:
            async with httpx.AsyncClient() as client:
                await client.post(f"{config['api_url']}/reload-prompt")
        except Exception:
            pass

        return RedirectResponse(f"/manage/{bot_id}?success=true", status_code=303)
    else:
        return RedirectResponse(
            f"/manage/{bot_id}?error=Error+saving+prompt+file",
            status_code=303
        )

@app.post("/release-chat/{bot_id}")
async def release_chat(
        request: Request,
        bot_id: str,
        chat_id: str = Form(...)
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    config = BOT_CONFIG[bot_id]

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{config['api_url']}/release-chat",
                json={"chat_id": chat_id}
            )
            if response.status_code == 200:
                return RedirectResponse(f"/manage/{bot_id}?success=true", status_code=303)
            else:
                return RedirectResponse(f"/manage/{bot_id}?error=Failed+to+release+chat", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/manage/{bot_id}?error=Error+releasing+chat", status_code=303)

import time
from datetime import datetime, timedelta
import urllib.parse

@app.get("/costs/{bot_id}", response_class=HTMLResponse)
async def bot_costs(
    request: Request,
    bot_id: str,
    period: str = Query("today", description="Time period: today, week, month, all")
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"Bot {bot_id} not found"
        }, status_code=404)

    config = BOT_CONFIG[bot_id]

    # Get OpenAI admin key and project ID from config
    openai_admin_key = config.get("openai_admin_key")
    project_id = config.get("project_id")

    if not openai_admin_key:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "OpenAI admin key not configured for this bot"
        })

    if not project_id:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Project ID not configured for this bot"
        })

    # Calculate time range based on period
    now = datetime.utcnow()
    if period == "today":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now
        limit = 1
    elif period == "week":
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now
        limit = 7
    elif period == "month":
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_date = now
        limit = 30
    else:  # all time
        start_date = datetime(2023, 11, 1)  # OpenAI API was launched around this time
        end_date = now
        limit = 180

    # Convert to Unix timestamps
    start_time = int(start_date.timestamp())
    end_time = int(end_date.timestamp())

    # Ensure end_time is after start_time
    if end_time <= start_time:
        end_time = start_time + 86400  # Add one day

    # Prepare API request parameters
    params = {
        "start_time": start_time,
        "bucket_width": "1d",
        "limit": limit,
        "project_ids": [project_id,]
    }

    try:
        async with httpx.AsyncClient() as client:
            # Call OpenAI Costs API directly
            headers = {
                "Authorization": f"Bearer {openai_admin_key}",
                "Content-Type": "application/json",
            }

            response = await client.get(
                "https://api.openai.com/v1/organization/costs",
                params=params,
                headers=headers
            )

            if response.status_code != 200:
                return templates.TemplateResponse("error.html", {
                    "request": request,
                    "error": f"OpenAI API returned error: {response.status_code} - {response.text}"
                })

            cost_data = response.json()

    except httpx.ConnectError:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Failed to connect to OpenAI API"
        })
    except Exception as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"Unexpected error: {str(e)}"
        })

    # Process the cost data from OpenAI API response
    total_cost = 0
    daily_costs = []

    if "data" in cost_data:
        for bucket in cost_data["data"]:
            bucket_cost = 0
            for result in bucket.get("results", []):
                amount = result.get("amount", {})
                cost_value = amount.get("value", 0)
                bucket_cost += float(cost_value)
                total_cost += float(cost_value)

            daily_costs.append({
                "date": datetime.fromtimestamp(bucket["start_time"]).strftime("%Y-%m-%d"),
                "cost": bucket_cost
            })

    return templates.TemplateResponse("costs.html", {
        "request": request,
        "bot_id": bot_id,
        "bot_name": f"Bot {bot_id}",
        "total_cost": f"${total_cost:.6f}",
        "daily_costs": daily_costs,
        "period": period,
        "period_options": ["today", "week", "month", "all"]
    })

@app.get("/clients/{bot_id}", response_class=HTMLResponse)
async def clients_list(
        request: Request,
        bot_id: str,
        temperature: Optional[str] = Query(None, description="Filter by temperature: горячий, теплый, холодный"),
        manager_status: Optional[str] = Query(None, description="Filter by manager status: Зелёный, Жёлтый, Красный"),
        page: int = Query(1, description="Page number", ge=1)
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    clients_data = await get_clients(bot_id, temperature, manager_status, page, 20)

    return templates.TemplateResponse("clients.html", {
        "request": request,
        "bot_id": bot_id,
        "bot_name": f"Bot {bot_id}",
        "clients": clients_data["clients"],
        "current_temperature_filter": temperature,
        "current_manager_status_filter": manager_status,
        "temperature_options": ["горячий", "теплый", "холодный"],
        "manager_status_options": ["Зелёный", "Жёлтый", "Красный"],
        "pagination": {
            "current_page": clients_data["current_page"],
            "total_pages": clients_data["total_pages"],
            "has_prev": clients_data["has_prev"],
            "has_next": clients_data["has_next"],
            "total_clients": clients_data["total_clients"]
        }
    })

@app.post("/update-client/{bot_id}/{user_id}")
async def update_client_handler(
        request: Request,
        bot_id: str,
        user_id: str,
        name: Optional[str] = Form(None),
        phone: Optional[str] = Form(None),
        manager_status: Optional[str] = Form(None),
        manager_comment: Optional[str] = Form(None)
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    # Get current client data if name/phone are not provided
    if name is None or phone is None:
        try:
            async with pool.acquire() as conn:
                current_client = await conn.fetchrow(
                    "SELECT name, phone FROM clients WHERE user_id = $1 AND bot_id = $2",
                    user_id, bot_id
                )
                if current_client:
                    name = name or current_client['name'] or ""
                    phone = phone or current_client['phone'] or ""
        except Exception as e:
            print(f"Error getting current client data: {str(e)}")
            # Continue with empty values if we can't get current data

    if await update_client(bot_id, user_id, name or "", phone or "", manager_status, manager_comment):
        return RedirectResponse(f"/clients/{bot_id}?success=true", status_code=303)
    else:
        return RedirectResponse(f"/clients/{bot_id}?error=Error+updating+client", status_code=303)

@app.post("/add-product")
async def add_product_handler(
        request: Request,
        name: str = Form(...),
        description: str = Form(...),
        price: str = Form(...),
        image: UploadFile = File(None)
):
    username = request.cookies.get("username")
    bot_id = request.cookies.get("bot_id")

    if not username or not bot_id:
        return RedirectResponse(url="/", status_code=303)

    image_path = None

    if image and image.filename:
        file_ext = os.path.splitext(image.filename)[1]
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}{file_ext}"
        image_path = f"static/uploads/{filename}"

        with open(image_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)

    if await add_product(name, description, price, image_path, bot_id):
        return RedirectResponse(f"/manage/{bot_id}?success=true", status_code=303)
    else:
        return RedirectResponse(f"/manage/{bot_id}?error=Error+adding+product", status_code=303)

@app.post("/delete-product/{product_id}")
async def delete_product_handler(
        request: Request,
        product_id: int
):
    username = request.cookies.get("username")
    bot_id = request.cookies.get("bot_id")

    if not username or not bot_id:
        return RedirectResponse(url="/", status_code=303)

    if await delete_product(product_id, bot_id):
        return RedirectResponse(f"/manage/{bot_id}?success=true", status_code=303)
    else:
        return RedirectResponse(f"/manage/{bot_id}?error=Error+deleting+product", status_code=303)

# Mailing routes
@app.get("/mailing/{bot_id}", response_class=HTMLResponse)
async def mailing_page(
    request: Request,
    bot_id: str,
    success: bool = False,
    error: str = None
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    # Get mailing campaigns
    campaigns = await get_mailing_campaigns(bot_id)

    return templates.TemplateResponse("mailing.html", {
        "request": request,
        "bot_id": bot_id,
        "bot_name": f"Bot {bot_id}",
        "campaigns": campaigns,
        "success": success,
        "error_msg": error
    })

@app.post("/create-mailing-campaign/{bot_id}")
async def create_mailing_campaign_handler(
    request: Request,
    bot_id: str,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    message: str = Form(...),
    delay_seconds: int = Form(2),
    excel_file: UploadFile = File(None)
):
    username = request.cookies.get("username")
    bot_id_cookie = request.cookies.get("bot_id")

    if not username or not bot_id_cookie or bot_id_cookie != bot_id:
        return RedirectResponse(url="/", status_code=303)

    if bot_id not in BOT_CONFIG:
        raise HTTPException(status_code=404, detail="Bot not found")

    excel_file_path = None

    # Save Excel file if provided
    if excel_file and excel_file.filename:
        if not excel_file.filename.endswith(('.xlsx', '.xls')):
            return RedirectResponse(
                f"/mailing/{bot_id}?error=Only+Excel+files+are+allowed",
                status_code=303
            )

        file_ext = os.path.splitext(excel_file.filename)[1]
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}{file_ext}"
        excel_file_path = f"static/excel/{filename}"

        with open(excel_file_path, "wb") as buffer:
            shutil.copyfileobj(excel_file.file, buffer)

    try:
        # Create campaign
        campaign_id = await create_mailing_campaign(
            bot_id=bot_id,
            name=name,
            message=message,
            excel_file_path=excel_file_path,
            delay_seconds=delay_seconds
        )

        # Start processing in background
        background_tasks.add_task(process_mailing_campaign, campaign_id)

        return RedirectResponse(
            f"/mailing/{bot_id}?success=true",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Error creating mailing campaign: {str(e)}")
        return RedirectResponse(
            f"/mailing/{bot_id}?error=Error+creating+campaign",
            status_code=303
        )


@app.post("/start-campaign/{campaign_id}")
async def start_campaign_handler(
    request: Request,
    campaign_id: int,
    background_tasks: BackgroundTasks
):
    username = request.cookies.get("username")
    bot_id = request.cookies.get("bot_id")

    if not username or not bot_id:
        return RedirectResponse(url="/", status_code=303)

    # Start processing in background
    background_tasks.add_task(process_mailing_campaign, campaign_id)

    return RedirectResponse(
        f"/mailing/{bot_id}?success=true",
        status_code=303
    )

@app.post("/delete-campaign/{campaign_id}")
async def delete_campaign_handler(
    request: Request,
    campaign_id: int
):
    username = request.cookies.get("username")
    bot_id = request.cookies.get("bot_id")

    if not username or not bot_id:
        return RedirectResponse(url="/", status_code=303)

    try:
        async with pool.acquire() as conn:
            # Get campaign to delete associated files
            campaign = await conn.fetchrow(
                "SELECT excel_file_path FROM mailing_campaigns WHERE id = $1 AND bot_id = $2",
                campaign_id, bot_id
            )

            if campaign and campaign['excel_file_path'] and os.path.exists(campaign['excel_file_path']):
                os.remove(campaign['excel_file_path'])

            # Delete campaign (cascade will delete contacts)
            await conn.execute(
                "DELETE FROM mailing_campaigns WHERE id = $1 AND bot_id = $2",
                campaign_id, bot_id
            )

        return RedirectResponse(
            f"/mailing/{bot_id}?success=true",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Error deleting campaign: {str(e)}")
        return RedirectResponse(
            f"/mailing/{bot_id}?error=Error+deleting+campaign",
            status_code=303
        )

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/")
    response.delete_cookie("username")
    response.delete_cookie("bot_id")
    return response

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
