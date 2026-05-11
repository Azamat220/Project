import express from 'express';
import path from 'path';
import {fileURLToPath} from 'url';
import { Pool } from 'pg';
import pgvector from 'pgvector/pg';
import initializeWhatsAppClient from './whatsapp.js';
import fs from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
app.use(express.json());
app.use(express.urlencoded({extended: true}));
const PROMPT_FILE = process.env.PROMPT_FILE || 'prompt.txt';

// PostgreSQL connection pool
const pool = new Pool({
  user: process.env.PGUSER || 'adminbek',
  host: process.env.PGHOST || 'postgres',
  database: process.env.PGDATABASE || 'moidb',
  password: process.env.PGPASSWORD || 'passwordbek',
  port: process.env.PGPORT || 5432,
});

// Initialize database schema
async function initializeDatabase() {
  const client = await pool.connect();

  try {
    // Register vector types for this client
    pgvector.registerTypes(client);

    // Create conversations table
    await client.query(`
      CREATE TABLE IF NOT EXISTS conversations_2 (
        id SERIAL PRIMARY KEY,
        user_id TEXT NOT NULL,
        bot_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        message TEXT NOT NULL,
        whatsapp_msg_id TEXT UNIQUE,
        is_bot BOOLEAN DEFAULT FALSE,
        media_type TEXT,
        timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
      );
    `);

    // Create indexes
    await client.query(`
      CREATE INDEX IF NOT EXISTS idx_user_id ON conversations_2 (user_id);
      CREATE INDEX IF NOT EXISTS idx_timestamp ON conversations_2 (timestamp);
    `);

    // Create products table
    await client.query(`
      CREATE TABLE IF NOT EXISTS products_2 (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        price TEXT,
        image TEXT,
        description TEXT,
        embedding vector(1536),
        created_at TIMESTAMPTZ DEFAULT NOW()
      );
    `);

    // Create unique index on product name
    await client.query(`
      CREATE UNIQUE INDEX IF NOT EXISTS products_name_idx
      ON products_2(name);
    `);

    // Create vector index
    await client.query(`
      CREATE INDEX IF NOT EXISTS products_embedding_idx
      ON products_2 USING hnsw (embedding vector_cosine_ops);
    `);

    console.log('✅ Database initialized successfully');
  } catch (err) {
    console.error('❌ Database initialization error:', err);
  } finally {
    client.release();
  }
}

// Initialize prompt file
if (!fs.existsSync(PROMPT_FILE)) {
    const DEFAULT_PROMPT = `
💼 Role: You're a friendly, professional assistant consultant for NevoDevs...
// ... (your full prompt here) ...
    `.trim();
    fs.writeFileSync(PROMPT_FILE, DEFAULT_PROMPT);
}

// Shared app state
const appState = {
    agentInstructions: fs.readFileSync(PROMPT_FILE, 'utf8'),
    latestQR: '',
    clientReady: false,
    db: pool,
    activeHumanChats: new Set(),
    chatTimers: new Map(),
    batchTimeout: 5000,
    autoReleaseTimeout: 1800000,
    botMessageIds: new Map(),
    processingMessages: new Set(),
    messageBuffers: {},
    seenMessageIds: new Set(),
    botSendingMessage: false
};

// Config handling
const configPath = `/data/bots/${process.env.BOT_ID}/config.json`;

function loadOrCreateConfig() {
    try {
        const configDir = path.dirname(configPath);
        if (!fs.existsSync(configDir)) {
            fs.mkdirSync(configDir, { recursive: true });
        }

        if (!fs.existsSync(configPath)) {
            const defaultConfig = {
                batch_timeout_seconds: 5,
                auto_release_minutes: 30
            };
            fs.writeFileSync(configPath, JSON.stringify(defaultConfig, null, 2));
            console.log(`⚙️ Created default config at ${configPath}`);
            return defaultConfig;
        }

        const fileContent = fs.readFileSync(configPath, 'utf8');
        if (!fileContent.trim()) throw new Error('Config file is empty');
        return JSON.parse(fileContent);
    } catch (err) {
        console.warn(`⚠️ Error loading config: ${err.message}`);
        return {
            batch_timeout_seconds: 5,
            auto_release_minutes: 30
        };
    }
}

function updateSettings(config) {
    appState.batchTimeout = config.batch_timeout_seconds * 1000;
    appState.autoReleaseTimeout = config.auto_release_minutes * 60 * 1000;
    console.log(`⚙️ Settings: Batch=${config.batch_timeout_seconds}s, Release=${config.auto_release_minutes}m`);
}

// Initialize config
try {
    const config = loadOrCreateConfig();
    updateSettings(config);
} catch (err) {
    console.error('❌ Failed to initialize settings:', err);
}

// API Endpoints
app.get('/api/status', (req, res) => {
    let phone = null;
    
    // Пытаемся получить информацию о клиенте, если он инициализирован
    if (appState.whatsAppClient && appState.whatsAppClient.info && appState.whatsAppClient.info.wid) {
        phone = appState.whatsAppClient.info.wid.user;
        // Добавляем плюс для красоты, если его нет
        if (phone && !phone.startsWith('+')) {
            phone = '+' + phone;
        }
    }

    res.json({ 
        ready: appState.clientReady,
        phone: phone 
    });
});

app.get('/api/qr', (req, res) => {
    res.json({ qr: appState.latestQR });
});

app.get('/api/active-chats', (req, res) => {
    try {
        res.json({ chats: Array.from(appState.activeHumanChats) });
    } catch (err) {
        res.status(500).json({ error: 'Failed to get active chats' });
    }
});

app.post('/api/reload-prompt', (req, res) => {
    try {
        appState.agentInstructions = fs.readFileSync(PROMPT_FILE, 'utf8');
        res.json({status: 'success'});
    } catch (err) {
        res.status(500).json({error: 'Failed to reload prompt'});
    }
});

app.post('/api/release-chat', (req, res) => {
    try {
        const { chat_id } = req.body;
        if (!chat_id) return res.status(400).json({ error: 'Missing chat_id' });

        if (appState.activeHumanChats.has(chat_id)) {
            appState.activeHumanChats.delete(chat_id);
            return res.json({ status: 'success' });
        }
        return res.status(404).json({ error: 'Chat not active' });
    } catch (err) {
        res.status(500).json({ error: 'Failed to release chat' });
    }
});

app.post('/api/update-settings', (req, res) => {
    try {
        const { batch_timeout_seconds, auto_release_minutes } = req.body;
        const newConfig = { batch_timeout_seconds, auto_release_minutes };
        updateSettings(newConfig);
        fs.writeFileSync(configPath, JSON.stringify(newConfig, null, 2));
        res.json({ status: 'success' });
    } catch (err) {
        res.status(500).json({ error: 'Failed to update settings' });
    }
});

// NEW ENDPOINT: Send message to WhatsApp
app.post('/api/send-message', async (req, res) => {
    try {
        const { chat_id, message } = req.body;
        
        if (!chat_id || !message) {
            return res.status(400).json({ 
                error: 'Missing required fields: chat_id and message are required' 
            });
        }

        if (!appState.clientReady) {
            return res.status(503).json({ 
                error: 'WhatsApp client is not ready. Please check if the bot is properly connected.' 
            });
        }

        // Get the WhatsApp client from appState
        const client = appState.whatsAppClient;
        if (!client) {
            return res.status(503).json({ 
                error: 'WhatsApp client not available' 
            });
        }

        console.log(`📤 Sending message to ${chat_id}: ${message.substring(0, 50)}...`);

        // Format chat_id if needed (ensure it has @c.us suffix)
        let formattedChatId = chat_id;
        if (!formattedChatId.includes('@')) {
            formattedChatId += '@c.us';
        }

        // Send the message
        const sentMessage = await client.sendMessage(formattedChatId, message);
        
        // Store the message ID to track it as a bot message
        if (sentMessage && sentMessage.id && sentMessage.id._serialized) {
            appState.botMessageIds.set(sentMessage.id._serialized, Date.now());
        }

        // Save to database
        try {
            await pool.query(
                `INSERT INTO conversations (user_id, role, message, bot_id, is_bot)
                 VALUES ($1, 'assistant', $2, $3, true)`,
                [formattedChatId, message, process.env.BOT_ID]
            );
        } catch (dbError) {
            console.error('❌ Failed to save sent message to database:', dbError);
            // Don't fail the request if DB save fails
        }

        res.json({ 
            status: 'success', 
            message_id: sentMessage.id._serialized,
            chat_id: formattedChatId
        });

    } catch (error) {
        console.error('❌ Error sending message:', error);
        res.status(500).json({ 
            error: 'Failed to send message: ' + error.message 
        });
    }
});

// Initialize database and start server
initializeDatabase().then(() => {
    // Store the WhatsApp client in appState when initialized
    const whatsAppClient = initializeWhatsAppClient(appState);
    appState.whatsAppClient = whatsAppClient;
    
    const PORT = process.env.PORT || 3000;
    const HOST = '0.0.0.0';
    app.listen(PORT, HOST, () => {
        console.log(`🚀 Server started on http://${HOST}:${PORT}`);
    });
}).catch(err => {
    console.error('❌ Failed to initialize database:', err);
    process.exit(1);
});
