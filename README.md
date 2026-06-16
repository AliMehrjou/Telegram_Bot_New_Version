[مشاهده نسخه فارسی (README.fa.md)](./README.fa.md)

# Anonymous Telegram Matching & Dating Bot

A highly scalable, production-ready Anonymous Dating & Matching Bot built for Telegram. This bot facilitates anonymous chat, smart location-based routing, gamified compatibility questionnaires, and features an internal coin economy.

## 🚀 Overview

This repository powers a fully asynchronous Telegram bot designed to handle thousands of concurrent users safely. It pairs users dynamically based on smart filters (province, city, gender) or randomly. To ensure safety and authenticity, it features an atomic coin-based economy, robust FSM (Finite State Machine) onboarding, automated timeouts for inactive matches, and privacy-preserving content filters.

## 🛠 Tech Stack

- **Language:** Python 3.11+
- **Bot Framework:** [aiogram 3.x](https://docs.aiogram.dev/en/latest/) (Fully Async)
- **Web Server:** FastAPI (for Telegram webhooks and Admin API)
- **Database:** MySQL 8.0 with SQLAlchemy 2.0 (Async)
- **Caching & Queueing:** Redis (aioredis) for FSM storage, atomic matching queues, and synchronization.
- **Infrastructure:** Docker & Docker Compose

## ✨ Key Features

- **Smart Matching Engine:** Utilizes Redis sets and atomic operations to match users accurately while enforcing strict BlockList checks (`SISMEMBER`) to prevent unwanted collisions.
- **Internal Coin Economy:** Users earn coins via referrals and profile completion. Advanced filters cost coins, which are securely deducted via atomic database transactions only *after* a successful match.
- **Gamified Compatibility (Dating Flow):** Users answer a 20-question survey synchronized via atomic Redis `INCR` locks. A 5-second asynchronous countdown (`asyncio.sleep`) prepares both users before the questions begin.
- **Robust API Error Handling:** Wraps all external Telegram API calls with strict `try/except` blocks (catching `TelegramForbiddenError` and `TelegramAPIError`) to prevent bot crashes when users block the bot mid-session.
- **Anonymous Chat Routing:** Content filters prevent users from sending locations, contacts, or identifiable tokens (like phone numbers or usernames).

## ⚙️ Environment Variables Setup

Create a `.env` file in the root directory and configure the following parameters:

```env
# Bot Configuration
BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
BOT_USERNAME=@YourBotUsername
SUPPORT_USERNAME=@YourSupportHandle
ADMIN_IDS=12345678,87654321

# Webhook & FastAPI
HOST=0.0.0.0
PORT=8000
BASE_URL=https://yourdomain.com/v1/webhook

# Database Configuration (MySQL)
DB_USER=root
DB_PASSWORD=secret
DB_HOST=db
DB_PORT=3306
DB_NAME=dating_bot

# Redis Configuration
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=secret

# Channel Requirements
REQUIRED_CHANNEL_ID=-1001234567890
CHANNEL_INVITE_LINK=https://t.me/your_channel
```

## 💻 Local Setup & Installation

Follow these steps to launch the bot locally using Docker.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-repo/matching_bot_project.git
   cd matching_bot_project
   ```

2. **Configure environment variables:**
   Copy the `.env.example` to `.env` and fill in your credentials.
   ```bash
   cp .env.example .env
   ```

3. **Start services with Docker Compose:**
   This will spin up the MySQL database, Redis, and the Python bot container.
   ```bash
   docker-compose up -d --build
   ```

4. **Verify operations:**
   Check the logs to ensure the dispatcher and FastAPI server are running smoothly.
   ```bash
   docker-compose logs -f bot
   ```
