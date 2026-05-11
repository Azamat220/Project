FROM node:20-slim

# Install dependencies for Chromium
RUN apt-get update && apt-get install -y \
    wget \
    git \
    ca-certificates \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    xdg-utils \
    --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
RUN apt-get update && apt-get install -y ffmpeg
# Set working directory
WORKDIR /app

# Copy package files first for better caching
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy all source files
COPY . .

# Environment variables
ENV OPENAI_API_KEY=${OPENAI_API_KEY}
ENV ADMIN_USER=${ADMIN_USER}
ENV ADMIN_PASS=${ADMIN_PASS}
ENV PORT=3001
ENV EMBEDDING_MODEL=text-embedding-3-small
ENV SIMILARITY_THRESHOLD=0.7
ENV SUPABASE_URL=${SUPABASE_URL}
ENV SUPABASE_KEY=${SUPABASE_KEY}
ENV GOOGLE_CREDS=${GOOGLE_CREDS}
ENV GOOGLE_SHEET_ID=${GOOGLE_SHEET_ID}
ENV GOOGLE_SHEET_NAME=${GOOGLE_SHEET_NAME}
# Expose port
EXPOSE 3001

# Use the actual filename of your entry point
CMD [ "node", "server.js" ]
