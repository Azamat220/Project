import qrcode from 'qrcode';
import pkg from 'whatsapp-web.js';
import { tmpdir } from 'os';
import { join } from 'path';
import { writeFile } from 'fs/promises';
import { readFileSync } from 'fs';
import fs from 'fs/promises';
import path from 'path';
import { transcribeAudio } from './transcribe.js';
import OpenAI from 'openai';
import { fileTypeFromBuffer } from 'file-type';
import fetch from 'node-fetch';

const { Client, LocalAuth, MessageMedia } = pkg;

const SUPPORTED_IMAGE_TYPES = [
  'image/jpeg',
  'image/png',
  'image/gif',
  'image/webp'
];

const openai = new OpenAI({apiKey: process.env.OPENAI_API_KEY});
const MAX_HISTORY = 50;

// Define tool for product information
const productTool = {
  type: "function",
  function: {
    name: "get_product_info",
    description: "Retrieve information about car accessories including price and image",
    parameters: {
      type: "object",
      properties: {
        product_name: {
          type: "string",
          description: "Name of the car accessory product"
        }
      },
      required: ["product_name"]
    }
  }
};


// Telegram Bot tool for sending lead notifications (Furniture Store)
const telegramTool = {
  type: "function",
  function: {
    name: "send_telegram_notification",
    description: "Send customer lead information to Telegram group when they are ready to buy",
    parameters: {
      type: "object",
      properties: {
        client_name: {
          type: "string",
          description: "Customer's full name"
        },
        phone_number: {
          type: "string",
          description: "Customer's contact phone number"
        },
        delivery_address: {
          type: "string",
          description: "Full shipping or delivery address"
        },
        product_interest: {
          type: "string",
          description: "Name of the product ordered"
        },
        clothing_size: {
          type: "string",
          description: "Size of the clothing (e.g., 'XS', 'S', 'M', 'L', 'XL', '42', '44')"
        },
        order_amount: {
          type: "string",
          description: "Total order amount (e.g., '15000 soms')"
        },
        delivery_method: {
          type: "string",
          description: "Selected delivery method (e.g., courier, self-pickup, postal)"
        },
        lead_source: {
          type: "string",
          description: "Where the customer came from (e.g., Instagram, Facebook ads, Website)"
        },
        additional_notes: {
          type: "string",
          description: "Any additional notes, comments, or special requests from the customer"
        }
      },
      required: ["client_name", "phone_number", "delivery_address", "product_interest", "clothing_size", "order_amount", "delivery_method"]
    }
  }
};

// Функция для приведения номера к формату +996XXXXXXXXX
function formatPhoneNumber(phone) {
  if (!phone) return '';

  // 1. Убираем всё лишнее (скобки, пробелы, дефисы), оставляем только цифры
  let cleaned = phone.replace(/\D/g, '');

  // 2. Логика для Кыргызстана
  // Вариант: 500123123 (9 цифр) -> добавляем +996
  if (cleaned.length === 9) {
    return `+996${cleaned}`;
  }

  // Вариант: 0500123123 (10 цифр, начинается с 0) -> убираем 0, добавляем +996
  if (cleaned.length === 10 && cleaned.startsWith('0')) {
    return `+996${cleaned.substring(1)}`;
  }

  // Вариант: 996500123123 (12 цифр) -> просто добавляем плюс
  if (cleaned.length === 12 && cleaned.startsWith('996')) {
    return `+${cleaned}`;
  }

  // Если номер не похож на KG (например, РФ или другой), возвращаем "как есть", но с плюсом
  return `+${cleaned}`;
}

// Function to send message to Telegram group
async function sendToTelegramGroup(orderData, userId) {
  try {
    const telegramBotToken = process.env.TELEGRAM_BOT_TOKEN;
    const telegramChatId = process.env.TELEGRAM_CHAT_ID;

    if (!telegramBotToken || !telegramChatId) {
      console.error('❌ Telegram credentials not configured');
      return 'Telebot credentials not configured';
    }
    const formattedPhone = formatPhoneNumber(userId);

    // Для ссылки убираем плюс, чтобы было wa.me/996...
    const phoneForLink = formattedPhone.replace('+', '');
    const waLink = `https://wa.me/${phoneForLink}`;

    const message = `*🛍️ НОВЫЙ ЗАКАЗ С WHATSAPP 🛍️*\n\n` +
                   `*👤 Клиент:* ${orderData.client_name}\n` +
                   `*👗 Товар:* ${orderData.product_interest}\n` +
                   `*📏 Размер:* ${orderData.clothing_size}\n` +
                   `—\n` +
                   `*💰 Сумма заказа:* ${orderData.order_amount}\n` +
                   `*🚚 Способ доставки:* ${orderData.delivery_method}\n` +
                   `*📍 Адрес:* ${orderData.delivery_address}\n\n` +
                   `*📞 Телефон:* ${formatPhoneNumber(orderData.phone_number)}\n` +
                   `💬 [Написать в WhatsApp](${waLink})\n\n` +
                   (orderData.lead_source ? `*📢 Источник:* ${orderData.lead_source}\n` : '') +
                   (orderData.additional_notes ? `*📝 Примечания:* ${orderData.additional_notes}\n\n` : '\n') +
                   `*✅ ЧЕК БЫЛ ОПЛАЧЕН*\n\n` +
                   `*⚠️ ТРЕБУЕТСЯ ДЕЙСТВИЕ:* Подготовить товар к отправке!`;

    const url = `https://api.telegram.org/bot${telegramBotToken}/sendMessage`;

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        chat_id: telegramChatId,
        text: message,
        parse_mode: 'Markdown',
        disable_web_page_preview: true,
        disable_notification: false
      })
    });

    const result = await response.json();

    if (result.ok) {
      console.log('✅ Lead notification sent to Telegram successfully');
      return 'Lead notification sent to Telegram successfully';
    } else {
      console.error('❌ Failed to send to Telegram:', result.description);
      return `Failed to send to Telegram: ${result.description}`;
    }
  } catch (error) {
    console.error('❌ Telegram send error:', error);
    return `Telegram send error: ${error.message}`;
  }
}


async function getEmbeddingFromOpenAI(text) {
  const response = await openai.embeddings.create({
    model: "text-embedding-3-small",
    input: text,
  });
  const embedding = response.data[0].embedding;
  return `[${embedding.join(',')}]`
}

// Vector-based product search using PostgreSQL
async function vectorProductSearch(query, appState) {
  try {
    const client = await appState.db.connect();

    try {
      const cleanQuery = query.trim();

      // 1. Сначала пробуем найти по точному совпадению в названии (обычный SQL поиск)
      // ILIKE означает поиск без учета регистра. %query% ищет вхождение слова.
      const textSearchResult = await client.query(`
        SELECT name, description, price, image
        FROM products
        WHERE LOWER(TRIM(name)) = LOWER($1)
        LIMIT 1;
      `, [cleanQuery]);

      // Если нашли товары по названию — возвращаем их
      if (textSearchResult.rows.length > 0) {
        console.log(`🔍 Найдено по названию: ${textSearchResult.rows[0].name}`);
        return textSearchResult.rows;
      }

      // 2. Если по названию ничего не нашли, делаем векторный поиск (умный поиск)
      console.log('🤖 Точное совпадение не найдено, включаем векторный поиск...');

      const embeddingSql = await getEmbeddingFromOpenAI(query);

      const result = await client.query(`
        SELECT name, description, price, image,
               1 - (embedding <=> $1::vector) AS similarity
        FROM products
        WHERE (1 - (embedding <=> $1::vector)) > 0.35
        ORDER BY similarity DESC
        LIMIT 1;
      `, [embeddingSql]);
      console.log(result.rows, 'log in vector')
      return result.rows;
    } finally {
      client.release();
    }
  } catch (err) {
    console.error('❌ Product search failed:', err);
    return [];
  }
}

// Get product info using search
async function getProductInfo(query, appState) {
  return await vectorProductSearch(query, appState);
}

// Format product information for OpenAI tool response
function formatProductForTool(product) {
  return {
    name: product.name,
    description: product.description || '',
    price: product.price || 'Не указана'
  };
}

// Download and prepare product image for WhatsApp
async function prepareProductImage(product) {
  try {
    if (!product.image) return null;

    const localPath = path.resolve(product.image);
    const imageBuffer = await fs.readFile(localPath);

    const type = await fileTypeFromBuffer(imageBuffer);
    if (!type || !SUPPORTED_IMAGE_TYPES.includes(type.mime)) {
      throw new Error(`Unsupported image type: ${type?.mime || 'unknown'}`);
    }

    return new MessageMedia(
      type.mime,
      imageBuffer.toString('base64'),
      path.basename(localPath)
    );
  } catch (err) {
    console.error(`❌ Failed to prepare product image for ${product.name}:`, err);
    return null;
  }
}

// Format product information as text
function formatProductText(product) {
  if (product.name.includes("QR")) {
    return `${product.description}`;
  } else {
    return `*${product.name}*\n${product.description ? `${product.description}` : ''}\n\n*Цена: ${product.price}*`;
  }
}

export default function initializeWhatsAppClient(appState) {
  // Initialize state for message handling
  appState.messageBuffers = {};
  appState.seenMessageIds = new Set();
  appState.activeHumanChats = appState.activeHumanChats || new Set();
  appState.chatTimers = appState.chatTimers || new Map();
  appState.botSendingMessage = false;
  appState.botMessageIds = new Map();
  appState.processingMessages = new Set();
  
  // NEW: Track manager message counts per chat
  appState.managerMessageCounts = new Map();

  const client = new Client({
    authStrategy: new LocalAuth({ clientId: process.env.CLIENT_ID || 'default' }),
    puppeteer: {
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--no-first-run',
        '--no-zygote',
        '--single-process'
      ]
    }
  });

  // WhatsApp event handlers
  client.on('qr', async (qr) => {
    console.log('📱 QR Code received');
    appState.latestQR = await qrcode.toDataURL(qr);

    appState.clientReady = false;
  });

  let ourNumber = null;

  client.on('ready', async () => {
    console.log('✅ WhatsApp ready!');
    appState.clientReady = true;

    try {
      const me = await client.getNumberId(client.info.wid.user);
      ourNumber = me._serialized;
      console.log(`🤖 Our bot number: ${ourNumber}`);
    } catch (err) {
      console.error('❌ Error getting bot number:', err);
      ourNumber = client.info.wid._serialized;
      console.log(`🤖 Using fallback bot number: ${ourNumber}`);
    }
  });

  client.on('disconnected', (reason) => {
    console.log('❌ WhatsApp disconnected:', reason);
    appState.clientReady = false;
  });

  client.on('auth_failure', (msg) => {
    console.error('❌ WhatsApp authentication failed:', msg);
    appState.clientReady = false;
  });

  let imagePrompt = "Опишите это изображение подробно";
  try {
    if (process.env.IMAGE_PROMPT_FILE) {
      imagePrompt = readFileSync(process.env.IMAGE_PROMPT_FILE, 'utf8');
      console.log(`🖼️ Loaded image prompt: ${imagePrompt.substring(0, 50)}...`);
    }
  } catch (err) {
    console.error('❌ Error loading image prompt:', err);
  }

  // Analyze images with OpenAI Vision
  async function analyzeImage(imageBuffer, userContext = '') {
    try {
      const type = await fileTypeFromBuffer(imageBuffer);
      if (!type || !SUPPORTED_IMAGE_TYPES.includes(type.mime)) {
        const detectedType = type?.mime || 'unknown';
        console.error(`❌ Unsupported image type: ${detectedType}`);
        return `[Неподдерживаемый формат изображения: ${detectedType}]`;
      }

      const base64Image = imageBuffer.toString('base64');
      const imageUrl = `data:${type.mime};base64,${base64Image}`;
      const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

      const prompt = userContext
        ? `Ответьте на вопрос пользователя об этом изображении: "${userContext}"` + ' ' + imagePrompt
        : imagePrompt;

      const response = await openai.chat.completions.create({
        model: "gpt-4o",
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: prompt },
              {
                type: "image_url",
                image_url: { url: imageUrl, detail: "high" }
              }
            ]
          }
        ],
        max_tokens: 1000,
        temperature: 0.2
      });

      return response.choices[0].message.content;
    } catch (err) {
      console.error('❌ Image analysis error:', err);
      return '[Не удалось обработать изображение]';
    }
  }

  // Process a batch of messages for a user
  async function processUserBatch(userId, appState, client) {
    if (appState.processingMessages.has(userId)) {
      console.log(`⏳ Already processing batch for ${userId}, skipping`);
      return;
    }

    try {
      appState.processingMessages.add(userId);

      const buffer = appState.messageBuffers[userId];
      if (!buffer || buffer.messages.length === 0) {
        delete appState.messageBuffers[userId];
        return;
      }

      const messagesToProcess = [...buffer.messages];
      buffer.messages = [];
      delete appState.messageBuffers[userId];

      console.log(`🔄 Processing batch of ${messagesToProcess.length} messages for ${userId}`);

      // Sort messages by timestamp
      messagesToProcess.sort((a, b) => a.timestamp - b.timestamp);

      // Group related messages
      const groupedMessages = [];
      let currentImage = null;

      for (const msg of messagesToProcess) {
        if (msg.type === 'image') {
          currentImage = msg;
        } else if (currentImage && msg.type === 'text') {
          currentImage.context = msg.content;
          groupedMessages.push(currentImage);
          currentImage = null;
        } else if (msg.type === 'text') {
          groupedMessages.push(msg);
        } else if (msg.type === 'audio') {
          groupedMessages.push(msg);
        }
      }

      if (currentImage) {
        groupedMessages.push(currentImage);
      }

      // Resolve all media in parallel
      const resolvedMessages = await Promise.all(
        groupedMessages.map(async (msg) => {
          try {
            let content = msg.content;

            if (msg.type === 'image') {
              content = await analyzeImage(msg.mediaBuffer, msg.context || '');
            } else if (msg.type === 'audio') {
              content = await content;
            }

            return {
              messageId: msg.messageId,
              content: content,
              type: 'text',
              originalType: msg.type
            };
          } catch (err) {
            console.error(`❌ Media processing failed for ${msg.messageId}:`, err);
            return {
              messageId: msg.messageId,
              content: `[Ошибка обработки ${msg.type}]`,
              type: 'text'
            };
          }
        })
      );

      // Combine messages into a single prompt
      let combinedPrompt = resolvedMessages.map(m => m.content).join('\n');
      console.log(`📝 Combined user prompt: ${combinedPrompt.substring(0, 80)}...`);

      // Save messages to database
      const db = appState.db;
      const dbClient = await db.connect();

      try {
        await dbClient.query('BEGIN');

        for (const msg of resolvedMessages) {
          try {
            await dbClient.query(
              `INSERT INTO conversations (user_id, role, message, whatsapp_msg_id, media_type, bot_id)
               VALUES ($1, 'user', $2, $3, $4, $5)
               ON CONFLICT (whatsapp_msg_id) DO NOTHING`,
              [userId, msg.content, msg.messageId, msg.originalType, process.env.BOT_ID]
            );
          } catch (err) {
            console.error('❌ Message insertion error:', err);
          }
        }

        await dbClient.query('COMMIT');
      } catch (err) {
        await dbClient.query('ROLLBACK');
        console.error('❌ Batch transaction failed:', err);
      } finally {
        dbClient.release();
      }

      // Get conversation history
      let history = [];
      let isFirstContact = true;

      try {
        const lastMsgResult = await db.query(
          `SELECT MAX(id) as max_id
           FROM conversations
           WHERE user_id = $1`,
          [userId]
        );

        const lastMsgBefore = lastMsgResult.rows[0]?.max_id || 0;

        const historyResult = await db.query(
          `SELECT role, message, media_type
           FROM conversations
           WHERE user_id = $1
             AND id <= $2
             AND id > $3
           ORDER BY timestamp ASC LIMIT $4`,
          [userId, lastMsgBefore, Math.max(0, lastMsgBefore - MAX_HISTORY), MAX_HISTORY]
        );

        history = historyResult.rows;

        const firstContactResult = await db.query(
          `SELECT COUNT(*) as count
           FROM conversations
           WHERE user_id = $1 AND role = 'assistant'`,
          [userId]
        );

        isFirstContact = firstContactResult.rows[0].count === 0;
      } catch (err) {
        console.error('❌ History query failed:', err);
      }

      if (isFirstContact) {
        console.log(`👋 First contact with user: ${userId}`);
        await new Promise(resolve => setTimeout(resolve, 2000));
      }

      // Prepare messages for OpenAI
      const messages = [
        {
          role: "system",
          content: `${appState.agentInstructions}\n\nТекущий контекст: ${isFirstContact ? "ПЕРВЫЙ КОНТАКТ" : "ПРОДОЛЖЕНИЕ РАЗГОВОРА"}`
        },
        ...history.map(({role, message, media_type}) => ({
          role: role === 'user' ? 'user' : 'assistant',
          content: media_type === 'image' ? `[Предыдущий анализ изображения: ${message}]` : message
        })),
        {role: "user", content: combinedPrompt}
      ];

      // Call OpenAI API with tool support
      try {
        const openai = new OpenAI({apiKey: process.env.OPENAI_API_KEY});
        const completion = await openai.chat.completions.create({
          model: "gpt-4o",
          messages,
          tools: [productTool, telegramTool],
          tool_choice: "auto",
          max_tokens: 1000,
          temperature: 0.7
        });

        let assistantReply = completion.choices[0].message.content;
        const toolCalls = completion.choices[0].message.tool_calls;
        let productsToSend = [];

        // Handle tool calls
        if (toolCalls && toolCalls.length > 0) {
          console.log(`🛠️ Tool calls detected: ${toolCalls.length}`);

          // Add assistant's tool call message to history
          messages.push(completion.choices[0].message);

          // Process each tool call
          for (const toolCall of toolCalls) {
            if (toolCall.function.name === 'get_product_info') {
              try {
                const args = JSON.parse(toolCall.function.arguments);
                const productQuery = args.product_name;
                console.log(`🛍️ Product query: ${productQuery}`);

                // Get product info
                const products = await getProductInfo(productQuery, appState);
                if (products.length > 0) {
                  console.log(`✅ Found ${products.length} products`);
                  productsToSend.push(...products);

                  // Add tool response to messages
                  messages.push({
                    tool_call_id: toolCall.id,
                    role: "tool",
                    name: "get_product_info",
                    content: JSON.stringify(products.map(formatProductForTool))
                  });
                } else {
                  messages.push({
                    tool_call_id: toolCall.id,
                    role: "tool",
                    name: "get_product_info",
                    content: "[]"
                  });
                }
              } catch (err) {
                console.error('❌ Tool call processing error:', err);
                messages.push({
                  tool_call_id: toolCall.id,
                  role: "tool",
                  name: "get_product_info",
                  content: "[]"
                });
              }
            } else if (toolCall.function.name === 'send_telegram_notification') {
                  try {
                    const args = JSON.parse(toolCall.function.arguments);
                    console.log('📤 Sending lead to Telegram:', args);

                    // Отправляем в Telegram
                    const telegramResult = await sendToTelegramGroup(args, userId);

                    // Добавляем результат в историю сообщений для GPT
                    messages.push({
                      tool_call_id: toolCall.id,
                      role: "tool",
                      name: "send_order_to_telegram",
                      content: telegramResult
                    });

                    // --- БАЗА ДАННЫХ (ОБНОВИТЕ ПОД НОВУЮ СТРУКТУРУ) ---
                    /*
                    try {
                      const orderDbClient = await appState.db.connect();
                      // Пример новой структуры:
                      await orderDbClient.query(
                        `INSERT INTO leads (
                          customer_id, product_interest, phone_number,
                          delivery_address, language, status, bot_id
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)`,
                        [
                          userId.split('@')[0],
                          args.product_interest,
                          args.phone_number,
                          args.delivery_address,
                          args.language || 'RU',
                          'new',
                          process.env.BOT_ID
                        ]
                      );
                      orderDbClient.release();
                      console.log('✅ Lead saved to database');
                    } catch (dbError) {
                      console.error('❌ Failed to save lead to database:', dbError);
                    }
                    */
                    // ---------------------------------------------------

                  } catch (err) {
                    console.error('❌ Telegram tool processing error:', err);
                    messages.push({
                      tool_call_id: toolCall.id,
                      role: "tool",
                      name: "send_order_to_telegram",
                      content: "Failed to process lead notification"
                    });
                  }
            }
          }

          // Get final assistant response with tool results
          const finalCompletion = await openai.chat.completions.create({
            model: "gpt-4o",
            messages,
            max_tokens: 1000,
            temperature: 0.7
          });

          assistantReply = finalCompletion.choices[0].message.content;
        }

        // Save response to database
        try {
          await db.query(
            `INSERT INTO conversations (user_id, role, message, bot_id)
             VALUES ($1, 'assistant', $2, $3)`,
            [userId, assistantReply, process.env.BOT_ID]
          );
        } catch (err) {
          console.error('❌ Failed to save assistant response:', err);
        }

        appState.botSendingMessage = true;

        // Only send products if we have them, otherwise send assistant reply
        if (productsToSend.length > 0) {
          console.log(`📦 Sending ${productsToSend.length} product(s) to ${userId}`);

          for (const product of productsToSend) {
            const media = await prepareProductImage(product);
            const productText = formatProductText(product);

            try {
              if (media) {
                // Send image with product details as caption
                const sent = await client.sendMessage(
                  userId,
                  media,
                  { caption: productText }
                );

                if (sent && sent.id && sent.id._serialized) {
                  appState.botMessageIds.set(sent.id._serialized, Date.now());
                }
                console.log(`✅ Product image sent for: ${product.name}`);
              } else {
                // Fallback to text if image fails
                await client.sendMessage(userId, productText);
              }

              // Добавляем задержку между отправками
              await new Promise(resolve => setTimeout(resolve, 1000));
            } catch (err) {
              console.error('❌ Product info send failed:', err);
            }
          }
        } else {
          // Send assistant reply only if there are no products
          try {
            const sent = await client.sendMessage(userId, assistantReply);
            if (sent && sent.id && sent.id._serialized) {
              appState.botMessageIds.set(sent.id._serialized, Date.now());
              console.log(`✅ Assistant message sent to ${userId}`);
            }
          } catch (err) {
            console.error('❌ Failed to send assistant message:', err);
          }
        }
      } catch (err) {
        console.error('❌ OpenAI API error:', err);
      } finally {
        setTimeout(() => {
          appState.botSendingMessage = false;
        }, 500);
      }
    } catch (err) {
      console.error('❌ Batch processing error:', err);
    } finally {
      appState.processingMessages.delete(userId);
    }
  }

  // Message handler with batching
  client.on('message', async (msg) => {
    if (msg.from.endsWith('@g.us') || msg.from === 'status@broadcast') {
      console.log(`🚫 Ignoring group or broadcast message from ${msg.from}`);
      return;
    }

    // 1. Сначала получаем контакт и чистый номер
    const contact = await msg.getContact();
    const phoneNumber = contact.number; // Чистый номер (например, 996558...)

    // 2. ПРИНУДИТЕЛЬНО формируем стандартный ID (@c.us)
    // Теперь неважно, пришло сообщение с @lid или @c.us - мы всегда работаем с единым форматом
    const userId = `${phoneNumber}@c.us`;

    const messageId = msg.id._serialized;
    const timestamp = Date.now();

    if (appState.seenMessageIds.has(messageId)) return;
    if (appState.activeHumanChats.has(userId)) {
      console.log(`⛔ Skipping message - human agent active for: ${userId}`);
      appState.seenMessageIds.add(messageId);
      return;
    }

    // Check database for duplicates
    try {
      const existingResult = await appState.db.query(
        `SELECT COUNT(*) as count
         FROM conversations
         WHERE whatsapp_msg_id = $1`,
        [messageId]
      );

      if (existingResult.rows[0]?.count > 0) {
        appState.seenMessageIds.add(messageId);
        return;
      }
    } catch (err) {
      console.error('❌ Duplicate check failed:', err);
    }

    appState.seenMessageIds.add(messageId);
    console.log(`📩 New message from ${userId}: ${msg.body?.substring(0, 20) || ''}${msg.body && msg.body.length > 20 ? '...' : ''}`);

    if (!appState.messageBuffers[userId]) {
      appState.messageBuffers[userId] = {
        timer: null,
        messages: []
      };
    }

    const userBuffer = appState.messageBuffers[userId];

    if (userBuffer.timer) {
      clearTimeout(userBuffer.timer);
    }
    userBuffer.timer = setTimeout(() => {
      processUserBatch(userId, appState, client);
    }, appState.batchTimeout);

    try {
      if (msg.hasMedia) {
        const media = await msg.downloadMedia();
        const mediaBuffer = Buffer.from(media.data, 'base64');

        if (media.mimetype.startsWith('image')) {
          userBuffer.messages.push({
            messageId,
            type: 'image',
            content: `[Обработка изображения...]`,
            mediaBuffer,
            context: msg.body || '',
            timestamp
          });
        }
        else if (media.mimetype.startsWith('audio')) {
          const filename = join(tmpdir(), `audio-${Date.now()}.${media.mimetype.split('/')[1] || 'ogg'}`);
          await writeFile(filename, mediaBuffer);

          const transcriptionPromise = transcribeAudio(filename)
            .then(result => {
              return result.text;
            });

          userBuffer.messages.push({
            messageId,
            type: 'audio',
            content: transcriptionPromise,
            timestamp
          });
        }
        else {
          userBuffer.messages.push({
            messageId,
            type: 'text',
            content: `[Неподдерживаемый тип медиа: ${media.mimetype}]`,
            timestamp
          });
        }
      } else if (msg.body) {
        userBuffer.messages.push({
          messageId,
          type: 'text',
          content: msg.body,
          timestamp
        });
      }
    } catch (err) {
      console.error('❌ Error processing message content:', err);
      userBuffer.messages.push({
        messageId,
        type: 'text',
        content: '[Ошибка обработки сообщения]',
        timestamp
      });
    }
  });

// Human agent detection
client.on('message_create', async (msg) => {
    try {
        const allowedTypes = ['chat', 'image', 'video', 'document', 'audio', 'ptt', 'sticker'];
        if (!allowedTypes.includes(msg.type)) return;

        const isFromMe = msg.fromMe || (msg.id && msg.id.fromMe);
        if (!isFromMe) return;

        if (msg.isStatus || msg.to === 'status@broadcast') return;

        const msgId = msg.id._serialized;

        if (appState.botSendingMessage) {
            appState.botMessageIds.set(msgId, Date.now());
            return; // Скрипт выйдет отсюда, но обязательно зайдет в блок finally!
        }
        
        if (appState.botMessageIds.has(msgId)) return;
        
        const now = Date.now();
        const recentBotMessages = Array.from(appState.botMessageIds.values())
             .some(timestamp => now - timestamp < 2000);
             
        if (recentBotMessages) {
             appState.botMessageIds.set(msgId, now);
             return;
        }

        // --- ЛОГИКА ПЕРЕХВАТА ---
        let targetCustomerId = null;
        let chat = null;

        try {
            chat = await msg.getChat();
            if (chat.isGroup) return;

            if (chat.id._serialized.includes('@c.us')) {
                targetCustomerId = chat.id._serialized;
            } else {
                 const contact = await chat.getContact();
                 if (contact.id._serialized.includes('@c.us')) {
                     targetCustomerId = contact.id._serialized;
                 }
            }
        } catch (e) {
            console.error('Error resolving customer ID:', e);
        }

        if (!targetCustomerId) return;

        let shouldStopBotImmediately = false;

        try {
            const recentMessages = await chat.fetchMessages({ limit: 5 });
            const history = recentMessages.filter(m => 
                m.id._serialized !== msgId && 
                allowedTypes.includes(m.type)
            );

            if (history.length === 0) {
                // Пустой чат (Таргет/Первое сообщение)
                shouldStopBotImmediately = false;
                console.log(`⏳ Target message sent to ${targetCustomerId}. Bot active.`);
            } else {
                // В чате уже что-то было (Перехват или второе сообщение таргета)
                shouldStopBotImmediately = true;
            }
        } catch (err) {
            console.error('Error checking context:', err);
            shouldStopBotImmediately = true;
        }

        // ИТОГОВОЕ РЕШЕНИЕ
        if (shouldStopBotImmediately) {
            console.log(`👨‍💼 Human Agent took over chat ${targetCustomerId}.`);
            
            appState.activeHumanChats.add(targetCustomerId);

            if (appState.chatTimers.has(targetCustomerId)) {
                clearTimeout(appState.chatTimers.get(targetCustomerId));
            }

            const timer = setTimeout(() => {
                appState.activeHumanChats.delete(targetCustomerId);
                appState.chatTimers.delete(targetCustomerId);
                console.log(`🤖 Bot resuming work for ${targetCustomerId} (timeout)`);
            }, appState.autoReleaseTimeout || 30 * 60 * 1000); 

            appState.chatTimers.set(targetCustomerId, timer);
        }

    } catch (globalErr) {
        console.error('Error in human handover logic:', globalErr);
    } finally {
        // --- CLEANUP OLD IDS ---
        // Этот блок гарантированно выполнится в конце, 
        // независимо от того, были ли return или ошибки выше
        if (appState.botMessageIds.size > 500) {
            const currentTime = Date.now();
            for (const [id, timestamp] of appState.botMessageIds.entries()) {
                if (currentTime - timestamp > 5 * 60 * 1000) { // Очищаем старше 5 минут
                    appState.botMessageIds.delete(id);
                }
            }
        }
    }
});

  // NEW: Reset manager message count when bot sends a message to a customer
  // This ensures the count resets when the bot takes over the conversation
  client.on('message', async (msg) => {
    if (msg.fromMe && msg.to && msg.to.includes('@c.us')) {
      // Check if this is a bot-generated message (not human agent)
      const msgId = msg.id._serialized;
      
      if (appState.botSendingMessage || appState.botMessageIds.has(msgId)) {
        // Reset the manager message count for this customer when bot sends a message
        appState.managerMessageCounts.delete(msg.to);
        console.log(`🔄 Reset manager message count for ${msg.to} - bot is responding`);
      }
    }
  });

  // Initialize client
  client.initialize();

  // Return the client instance so it can be used by the server
  return client;
}
