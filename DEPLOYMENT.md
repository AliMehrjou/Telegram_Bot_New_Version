# 🚀 Matching Bot v3.1 — Production Deployment Guide

این راهنمای کامل برای deploy پروژه با هدف پشتیبانی از **۲۰۰ هزار کاربر** است.

---

## 📋 فهرست

1. [پیش‌نیازها](#-پیش‌نیازها)
2. [معماری نهایی](#-معماری-نهایی)
3. [مرحله ۱: ساخت Bots در BotFather](#-مرحله-۱-ساخت-bots-در-botfather)
4. [مرحله ۲: آماده‌سازی سرور](#-مرحله-۲-آماده‌سازی-سرور)
5. [مرحله ۳: کانفیگ environment](#-مرحله-۳-کانفیگ-environment)
6. [مرحله ۴: SSL certificates](#-مرحله-۴-ssl-certificates)
7. [مرحله ۵: راه‌اندازی MySQL Primary + Replica](#-مرحله-۵-راه‌اندازی-mysql-primary--replica)
8. [مرحله ۶: اعمال migrationها](#-مرحله-۶-اعمال-migrationها)
9. [مرحله ۷: اجرای seeders](#-مرحله-۷-اجرای-seeders)
10. [مرحله ۸: build و start containers](#-مرحله-۸-build-و-start-containers)
11. [مرحله ۹: ثبت webhooks](#-مرحله-۹-ثبت-webhooks)
12. [مرحله ۱۰: تست سلامت](#-مرحله-۱۰-تست-سلامت)
13. [مرحله ۱۱: تنظیم monitoring](#-مرحله-۱۱-تنظیم-monitoring)
14. [Scaling چطور کار می‌کند](#-scaling-چطور-کار-می‌کند)
15. [اندازه‌ی Connection Pool دیتابیس](#-اندازه‌ی-connection-pool-دیتابیس)
16. [Backup و Disaster Recovery](#-backup-و-disaster-recovery)
17. [Troubleshooting](#-troubleshooting)

---

## 🔧 پیش‌نیازها

### سخت‌افزار پیشنهادی برای ۲۰۰K کاربر

| منبع | حداقل | پیشنهادی |
|------|------|----------|
| CPU cores | 8 | 16 |
| RAM | 16 GB | 32 GB |
| Disk (SSD) | 100 GB | 200 GB NVMe |
| Bandwidth | 100 Mbps | 1 Gbps |
| Public IP | 1 | 1 (با DNS) |

### نرم‌افزار

- Docker 24+ و Docker Compose v2+
- دامنه‌ی ثبت‌شده (با دسترسی به DNS records)
- حساب Zarinpal (برای درگاه پرداخت)
- ۵ تا ۱۰ bot token از @BotFather

---

## 🏗 معماری نهایی

```
                    ┌─────────────────────────────────────┐
                    │            اینترنت                  │
                    └──────────────┬──────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │       Nginx (TLS, LB)        │
                    │       Rate limiting          │
                    └──────────────┬───────────────┘
                                   │ least_conn
                ┌──────────────────┼──────────────────┐
                │                  │                  │
                ▼                  ▼                  ▼
       ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
       │ fastapi_bot │    │ fastapi_bot │    │ fastapi_bot │  (×4 replicas)
       │   #1        │    │   #2        │    │   #3,#4     │
       └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
              │                  │                  │
              └──────────┬───────┴──────────────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
       ┌────────────┐         ┌────────────┐
       │ MySQL      │ ←────── │ MySQL      │
       │ Primary    │ replica │ Replica    │
       │ (writes)   │         │ (reads)    │
       └────────────┘         └────────────┘

              ┌─────────────────────┐
              │   Redis (2GB)       │
              │  FSM + queues +     │
              │  cache              │
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
       ┌────────────┐         ┌────────────┐
       │ arq_worker │         │ arq_worker │  (×2 replicas)
       │   #1       │         │   #2       │
       └────────────┘         └────────────┘

              ┌─────────────────────┐
              │   Prometheus +      │
              │   Grafana           │
              └─────────────────────┘

              ┌─────────────────────┐
              │   5-10 Bot Shards   │  (each user → tg_id % num_shards)
              └─────────────────────┘
```

**ظرفیت نهایی:**
- ۵ bot × ۳۰ msg/sec = ۱۵۰ msg/sec outbound
- ۱۰ bot × ۳۰ msg/sec = ۳۰۰ msg/sec outbound
- ۲۰۰K کاربر → ~۲۰K همزمان فعال → ~۶۶۶ inbound updates/sec
- ۴ fastapi replica × ۵۰ request each = ۲۰۰ concurrent request

---

## 🤖 مرحله ۱: ساخت Bots در BotFather

برای scale به ۲۰۰K کاربر، به چند bot نیاز دارید (هر bot محدود به ۳۰ msg/sec است).

1. در تلگرام به **@BotFather** بروید
2. برای هر bot:
   ```
   /newbot
   ```
3. نام و username بدهید (مثلاً `MyDatingBot1`, `MyDatingBot2`, ...)
4. token هر bot را ذخیره کنید
5. برای هر bot، دستورات مشترک را set کنید:
   ```
   /setcommands
   ```
   - `start - شروع`
   - `qavanin - راهنما`
   - `referral - زیرمجموعه‌گیری`
   - `silent - حالت سایلنت`
   - `delete_account - حذف اکانت`

### توصیه: ۵ bot شروع کنید
- ۵ bot × ۳۰ msg/sec = ۱۵۰ msg/sec → کافی برای ~۲۰K کاربر همزمان
- اگر نیاز به بیشتر بود، به ۱۰ bot ارتقا دهید

---

## 🖥 مرحله ۲: آماده‌سازی سرور

### ۲.۱ نصب Docker

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Logout و دوباره login کنید

# Verify
docker --version
docker compose version
```

### ۲.۲ کانفیگ firewall

```bash
sudo ufw allow 22/tcp       # SSH
sudo ufw allow 80/tcp       # HTTP (redirect to HTTPS)
sudo ufw allow 443/tcp      # HTTPS
sudo ufw enable
```

### ۲.۳ افزایش file descriptor limit

```bash
echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf
```

---

## ⚙️ مرحله ۳: کانفیگ environment

### ۳.۱ clone پروژه

```bash
git clone <your-repo-url> match_bot
cd match_bot
```

### ۳.۲ کپی و ویرایش `.env`

```bash
cp .env.example .env
nano .env
```

### ۳.۳ مقادیر حیاتی که باید تنظیم کنید

```bash
# ─── Bot sharding (مهم برای 200K) ──────────────────────────────────────────
BOT_SHARD_TOKENS=1111111:AAEhBP0vXbG3YpJ8Z3rD5mF8K2tN6qL9VxYwZ-abcDEF,2222222:BBx...,3333333:CCy...

# ─── Database ──────────────────────────────────────────────────────────────
DB_ROOT_PASSWORD=<strong_random_password>
DB_NAME=match_bot_db
DB_USER=match_bot_user
DB_PASSWORD=<strong_random_password>
DATABASE_URL=mysql+asyncmy://match_bot_user:<password>@mysql_primary:3306/match_bot_db
DB_REPLICA_HOST=mysql_replica

# ─── Redis ─────────────────────────────────────────────────────────────────
REDIS_PASSWORD=<strong_random_password>

# ─── Domain ────────────────────────────────────────────────────────────────
BASE_URL=https://yourdomain.com
WEBHOOK_SECRET_TOKEN=<random_32_char_string>

# ─── Admin ─────────────────────────────────────────────────────────────────
ADMIN_USER_IDS=12345678,87654321
ADMIN_SECRET_TOKEN=<strong_random_token>
PRIMARY_NODE_ID=<your_telegram_user_id>  # for /sys_diag
SYSTEM_GUARD_SECRET_HASH=<paste the hash computed below — see note>

# ─── Payment ───────────────────────────────────────────────────────────────
PAYMENT_GATEWAY_ENABLED=true
ZARINPAL_MERCHANT_ID=<your_merchant_id>
ZARINPAL_SANDBOX=false
CARD_NUMBER_FOR_PAYMENT=۶۰۳۷۹۹۷۹۹۹۹۹۹۹۹۹
CARD_HOLDER_NAME=نام شما

# ─── Grafana ───────────────────────────────────────────────────────────────
GRAFANA_ADMIN_PASSWORD=<strong_password>

# ─── DB connection pool (defaults are fine unless you scale fastapi_bot/arq_worker replicas — see "Database Connection Pool Sizing" section below) ──
DB_POOL_SIZE=15
DB_MAX_OVERFLOW=30
```

⚠️ **`SYSTEM_GUARD_SECRET_HASH` نیاز به یه قدم جدا داره.** فایل `.env`
مستقیم parse می‌شه (با pydantic-settings / dotenv)، دستورات شل داخلش اجرا
نمی‌شن. اگه `$(echo -n "your_password" | sha256sum | cut -d' ' -f1)` رو
عیناً داخل `.env` بذارید، مقدار واقعی همون رشته‌ی متنی خام می‌مونه، نه
هش واقعی — و این فیچر امنیتی بی‌صدا از کار می‌افته. اول توی ترمینال (نه
داخل `.env`) هش رو بسازید:
```bash
echo -n "your_password" | sha256sum | cut -d' ' -f1
```
بعد فقط همون خروجی (یه رشته‌ی ۶۴ کاراکتری hex) رو کپی کنید و جلوی
`SYSTEM_GUARD_SECRET_HASH=` در `.env` بچسبونید.

---

## 🔒 مرحله ۴: SSL certificates

### ۴.۱ با Let's Encrypt (توصیه‌شده)

```bash
# Install certbot
sudo apt install certbot

# Stop nginx if running
docker compose down nginx 2>/dev/null || true

# Get certificate
sudo certbot certonly --standalone -d yourdomain.com -d www.yourdomain.com

# Copy certificates
sudo mkdir -p nginx/certs
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx/certs/
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx/certs/
sudo chown -R $USER:$USER nginx/certs/
```

### ۴.۲ Auto-renewal

```bash
# Add to crontab
sudo crontab -e
# Add this line:
0 3 * * * certbot renew --quiet && cp /etc/letsencrypt/live/yourdomain.com/*.pem /home/$USER/match_bot/nginx/certs/ && docker compose restart nginx
```

### ۴.۳ یا با self-signed (فقط برای تست)

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/certs/privkey.pem \
  -out nginx/certs/fullchain.pem \
  -subj "/C=IR/ST=Tehran/L=Tehran/O=MatchBot/CN=yourdomain.com"
```

---

## 🗄 مرحله ۵: راه‌اندازی MySQL Primary + Replica

### ۵.۱ شروع containers

```bash
# Start only DB and Redis first
docker compose up -d mysql_primary mysql_replica redis_cache

# Wait for healthy
docker compose ps
```

### ۵.۲ تنظیم replication

```bash
# On primary: create replication user
docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} << 'SQL'
CREATE USER 'replicator'@'%' IDENTIFIED BY '<strong_replica_password>';
GRANT REPLICATION SLAVE ON *.* TO 'replicator'@'%';
FLUSH PRIVILEGES;
SHOW MASTER STATUS;
SQL

# Note the File and Position from output, e.g.:
# File: mysql-bin.000003
# Position: 1234

# On replica: configure to follow primary
docker exec -i match_mysql_replica mysql -uroot -p${DB_ROOT_PASSWORD} << 'SQL'
CHANGE MASTER TO
  MASTER_HOST='mysql_primary',
  MASTER_USER='replicator',
  MASTER_PASSWORD='<strong_replica_password>',
  MASTER_LOG_FILE='mysql-bin.000003',  -- replace with actual File
  MASTER_LOG_POS=1234;                  -- replace with actual Position
START SLAVE;
SHOW SLAVE STATUS\G
SQL
```

### ۵.۳ بررسی replication

```bash
# Should show: Slave_IO_Running: Yes, Slave_SQL_Running: Yes
docker exec match_mysql_replica mysql -uroot -p${DB_ROOT_PASSWORD} -e "SHOW SLAVE STATUS\G" | grep Running
```

---

## 📦 مرحله ۶: اعمال migrationها

```bash
# Apply in order
docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} < database/migrations/001_add_indexes.sql
docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} < database/migrations/002_v3_columns.sql
docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} < database/migrations/003_v3_new_tables.sql
docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} < database/migrations/004_phase3_indexes_constraints.sql
# 005 is CRITICAL — without it, User.shard_index doesn't exist and every
# new-user registration AND every broadcast crashes immediately with
# "'shard_index' is an invalid keyword argument for User". Do not skip this
# one even on a fresh install — Base.metadata.create_all() (which runs on
# every app startup) does create the column on a brand-new database, but
# migration 005 is what adds it safely to a database that already exists
# from before this fix, and it's a no-op (safe to re-run) either way.
docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} < database/migrations/005_shard_index.sql

# Verify tables
docker exec match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} -e "SHOW TABLES;"

# Verify migration 005 specifically applied (should print one row: shard_index | int)
docker exec match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} -e "DESCRIBE users;" | grep shard_index
```

⚠️ **نکته درباره‌ی migration های 002 و 004:** این دو فایل از syntax
`ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` استفاده می‌کنن —
این syntax مخصوص MariaDB هست، نه MySQL. روی یه MySQL 8.0 واقعی (نه MariaDB)
تست شد و با خطای syntax (`ERROR 1064`) شکست می‌خوره. اگه از image رسمی
`mysql:8.0` (که همین docker-compose.yml استفاده می‌کنه) استفاده می‌کنید،
این دو migration رو قبل از اجرا با دستور بالا، جدا تست کنید — اگه خطا
گرفتید، باید بازنویسی بشن با الگوی `information_schema` + `PREPARE` (مثل
migration 005 که همین الگو رو داره).

---

## 🌱 مرحله ۷: اجرای seeders

```bash
# نکته: مسیر واقعی داخل کانتینر matching_bot_project/scripts/ هست، نه
# scripts/ (طبق Dockerfile: COPY . /app/matching_bot_project/ — فقط
# run.py و json_files جدا به ریشه‌ی /app کپی می‌شن، بقیه از جمله scripts/
# همون‌جا زیر matching_bot_project/ می‌مونن). با یه اجرای واقعی این مسیر
# رو تایید کردم.
docker compose run --rm fastapi_bot python matching_bot_project/scripts/seed_tags.py
docker compose run --rm fastapi_bot python matching_bot_project/scripts/seed_gifts.py
docker compose run --rm fastapi_bot python matching_bot_project/scripts/seed_vip_plans.py

# Verify
docker exec match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME} -e \
  "SELECT COUNT(*) FROM tag_catalog; SELECT COUNT(*) FROM gift_types;"
```

---

## 🚀 مرحله ۸: build و start containers

### ۸.۱ build image

```bash
docker compose build
```

### ۸.۲ شروع تمام سرویس‌ها

```bash
docker compose up -d
```

### ۸.۳ بررسی وضعیت

```bash
docker compose ps
# All services should show "healthy" or "Up"
```

### ۸.۴ مشاهده logs

```bash
docker compose logs -f fastapi_bot
docker compose logs -f arq_worker
```

---

## 🔗 مرحله ۹: ثبت webhooks

برای هر bot shard، باید webhook جداگانه ثبت کنید.

### ۹.۱ اسکریپت ثبت خودکار webhooks

```bash
cat > /tmp/register_webhooks.sh << 'EOF'
#!/bin/bash
# Reads BOT_SHARD_TOKENS from .env and registers webhook for each

source .env

IFS=',' read -ra TOKENS <<< "$BOT_SHARD_TOKENS"
for i in "${!TOKENS[@]}"; do
    TOKEN="${TOKENS[$i]}"
    TOKEN=$(echo "$TOKEN" | xargs)  # trim whitespace
    WEBHOOK_URL="https://${BASE_URL#https://}/api/v1/webhook?shard=${i}"
    echo "Registering shard $i → $WEBHOOK_URL"
    curl -s "https://api.telegram.org/bot${TOKEN}/setWebhook" \
        -d "url=${WEBHOOK_URL}" \
        -d "secret_token=${WEBHOOK_SECRET_TOKEN}" \
        -d "allowed_updates=%5B%22message%22%2C%22callback_query%22%2C%22my_chat_member%22%5D" \
        -d "drop_pending_updates=false" | jq .
done
EOF

chmod +x /tmp/register_webhooks.sh
/tmp/register_webhooks.sh
```

### ۹.۲ verify webhooks

```bash
source .env
IFS=',' read -ra TOKENS <<< "$BOT_SHARD_TOKENS"
for i in "${!TOKENS[@]}"; do
    TOKEN="${TOKENS[$i]}"
    TOKEN=$(echo "$TOKEN" | xargs)
    echo "Shard $i webhook info:"
    curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" | jq .
done
```

---

## ✅ مرحله ۱۰: تست سلامت

### ۱۰.۱ Health endpoint

```bash
curl -k https://yourdomain.com/health
# Expected: {"status":"healthy","service":"match_bot","engine":"alive"}
```

### ۱۰.۲ Shard info

```bash
curl -k https://yourdomain.com/shards
# Expected: {"num_shards":5,"is_sharded":true}
```

### ۱۰.۳ Metrics

```bash
curl -k https://yourdomain.com/metrics
# Expected: Prometheus-formatted metrics
```

### ۱۰.۴ تست عملی bot

1. در تلگرام، به اولین bot یک `/start` بفرستید
2. باید جواب بگیرید
3. به دومی `/start` بفرستید → باز هم باید جواب بگیرید
4. هر کاربر به‌طور خودکار به یک shard اختصاص داده می‌شود

---

## 📊 مرحله ۱۱: تنظیم monitoring

### ۱۱.۱ Prometheus

پیش‌تر با docker compose راه‌اندازی شده. verify:

```bash
docker compose ps prometheus
# Should be "Up"

# Open Prometheus UI (only accessible from localhost)
curl http://localhost:9090/-/healthy
```

### ۱۱.۲ Grafana

```bash
# Open Grafana
# (Grafana is exposed only on localhost:3000 — use SSH tunnel)
ssh -L 3000:localhost:3000 user@your-server

# Then open http://localhost:3000 in browser
# Default login: admin / ${GRAFANA_ADMIN_PASSWORD}
```

### ۱۱.۳ اضافه‌کردن Prometheus data source در Grafana

1. Configuration → Data Sources → Add data source
2. Choose Prometheus
3. URL: `http://prometheus:9090`
4. Save & Test

### ۱۱.۴ ساخت dashboard

Metrics مهم که باید monitor کنید:

| Metric | Description |
|--------|-------------|
| `bot_active_users` | کاربران فعال در ۵ دقیقه گذشته |
| `bot_match_queue_size` | کاربران در صف مچینگ |
| `bot_chat_active_sessions` | چت‌های فعال |
| `db_query_duration_seconds` | latency کوئری‌های DB |
| `db_pool_checked_out / db_pool_size` | اشباع connection pool |
| `redis_memory_used` | مصرف حافظه Redis |
| `bot_payments_processed_total` | پرداخت‌ها |
| `arq_jobs_total` | وضعیت jobهای پس‌زمینه |

---

## 📈 Scaling چطور کار می‌کند

### Bot Sharding (افزایش throughput تلگرام)

هر کاربر به یک shard اختصاص داده می‌شود:
```python
shard_index = tg_id % num_shards
```

- **مزیت:** کاربر همیشه با همان bot صحبت می‌کند (state ثابت)
- **مزیت:** throughput = N × 30 msg/sec
- **معایب:** کاربر نمی‌تواند به bot دیگری برود (مگر اینکه لینک بدیم)

### Read/Write Split

- **Primary (writes):** INSERT, UPDATE, DELETE
- **Replica (reads):** SELECT queries
- handlerها باید برای SELECT از `async_read_session_factory` استفاده کنند

### arq Workers

- broadcast، re-engagement، reminders در process جداگانه اجرا می‌شوند
- FastAPI فقط درخواست‌های webhook را پردازش می‌کند
- اگر arq_worker کرش کند، jobها در Redis باقی می‌مانند و worker دیگری آن‌ها را برمی‌دارد

### Horizontal Scaling با Docker Compose

```bash
# Scale to 8 fastapi_bot replicas
docker compose up -d --scale fastapi_bot=8 --scale arq_worker=4

# nginx automatically load-balances across all replicas
```

### زمان مناسب Scale up

| نشانه | اقدام |
|------|------|
| `bot_active_users` بالای ۱۰K | اضافه‌کردن bot شارد |
| `db_pool_checked_out / db_pool_size` بالای ۰.۸ | اضافه‌کردن fastapi replica |
| `db_query_duration_seconds` بالای ۱ ثانیه | اضافه‌کردن MySQL replica |
| `redis_memory_used` بالای ۸۰٪ | افزایش maxmemory یا اضافه‌کردن Redis |
| broadcast بیش از ۲ ساعت طول می‌کشد | اضافه‌کردن arq_worker replica |

---

## ⚖️ اندازه‌ی Connection Pool دیتابیس

هروقت طبق جدول بالا `fastapi_bot` یا `arq_worker` رو scale کردید، این بخش
رو هم دوباره چک کنید — مستقیماً به همون تصمیم وابسته‌ست.

برای جلوگیری از خطای `Too many connections` در MySQL، مجموع کانکشن‌های
تمام پروسه‌های پایتون (FastAPI + ARQ Workers) نباید از ۷۰٪ سقف
`max-user-connections` مای‌اسکیوال بگذره (پیش‌فرض ۴۰۰ برای Primary، ۳۸۰
برای Replica).

**فرمول محاسبه:**
`(تعداد replica فست‌ای‌پی‌آی + تعداد replica آرکیو) × (DB_POOL_SIZE + DB_MAX_OVERFLOW) <= 280`

**پیش‌فرض فعلی (۶ پروسه‌ی جمع):**
- FastAPI: ۴ replica
- ARQ Worker: ۲ replica
- `DB_POOL_SIZE`: ۱۵
- `DB_MAX_OVERFLOW`: ۳۰
- جمع = ۶ × ۴۵ = ۲۷۰ ✅ ایمن

⚠️ **مهم:** اگه `fastapi_bot` رو به بیش از ۴ replica افزایش دادید (مثلاً
`docker compose up -d --scale fastapi_bot=8`)، **باید** یا `DB_POOL_SIZE`
و `DB_MAX_OVERFLOW` رو در `.env` کم کنید، یا `max-user-connections` رو در
`primary.cnf` بالا ببرید و RAM بیشتری به کانتینر MySQL اختصاص بدید. این
دو تا env var از قبل در `.env.example` هستن، همون‌جا ویرایششون کنید.

---

## 💾 Backup و Disaster Recovery

### Daily MySQL backup

```bash
# Create backup script
cat > scripts/backup.sh << 'EOF'
#!/bin/bash
set -e
BACKUP_DIR=/backups/mysql
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)
docker exec match_mysql_primary mysqldump -uroot -p${DB_ROOT_PASSWORD} \
    --single-transaction --routines --triggers \
    ${DB_NAME} | gzip > $BACKUP_DIR/match_bot_$DATE.sql.gz

# Keep only last 7 days
find $BACKUP_DIR -name "match_bot_*.sql.gz" -mtime +7 -delete
EOF

chmod +x scripts/backup.sh

# Add to crontab (daily at 3 AM)
crontab -e
# Add:
0 3 * * * /home/$USER/match_bot/scripts/backup.sh
```

### Redis backup

```bash
# Redis AOF is already enabled in docker-compose.
# Manual snapshot:
docker exec match_redis_cache redis-cli -a ${REDIS_PASSWORD} BGSAVE
# Then copy /data/dump.rdb to backup location
```

### Restore procedure

```bash
# Stop app
docker compose stop fastapi_bot arq_worker

# Restore MySQL
gunzip < /backups/mysql/match_bot_20250101_030000.sql.gz | \
    docker exec -i match_mysql_primary mysql -uroot -p${DB_ROOT_PASSWORD} ${DB_NAME}

# Restart
docker compose start fastapi_bot arq_worker
```

---

## 🆘 Troubleshooting

### مشکل: bot جواب نمی‌دهد

```bash
# Check webhook status
curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" | jq

# Common issues:
# - "last_error_message": "Wrong response from the webhook: 502"
#   → Nginx یا FastAPI down است
# - "last_error_message": "SSL error"
#   → SSL certificate مشکل دارد
```

### مشکل: کرش در حین spike traffic

```bash
# Check fastapi logs
docker compose logs --tail=200 fastapi_bot

# Common issues:
# - "MySQL server has gone away" → pool_pre_ping را فعال کنید (در config هست)
# - "Too many connections" → max_connections را افزایش دهید
# - Redis OOM → maxmemory را افزایش دهید
```

### مشکل: replication lag

```bash
# Check replica lag
docker exec match_mysql_replica mysql -uroot -p${DB_ROOT_PASSWORD} \
    -e "SHOW SLAVE STATUS\G" | grep Seconds_Behind_Master

# Should be < 5 seconds. If high:
# - Increase innodb-buffer-pool-size on replica
# - Check for long-running queries on primary
```

### مشکل: arq worker کار نمی‌کند

```bash
# Check arq logs
docker compose logs arq_worker

# Common issues:
# - "Connection refused" → Redis down است
# - "Job timed out" → job_timeout را در WorkerSettings افزایش دهید
```

### مشکل: paygate callback کار نمی‌کند

```bash
# Verify callback URL is reachable
curl -k https://yourdomain.com/v1/payment/callback?order_id=1&Authority=test&Status=OK

# Should return HTML page (not 404 or 500)
```

### Recovery از موقعیت‌های بحرانی

#### Situation: تمام bots down
1. `docker compose restart fastapi_bot`
2. اگر حل نشد: `docker compose down && docker compose up -d`
3. اگر حل نشد: بررسی logs برای خطاهای import یا DB

#### Situation: MySQL primary down
1. Replica را به primary تبدیل کنید:
   ```sql
   STOP SLAVE;
   RESET SLAVE ALL;
   SET GLOBAL read_only=0;
   ```
2. در `.env`، `DB_HOST=mysql_replica` کنید
3. `docker compose restart fastapi_bot`

#### Situation: Redis down
1. `docker compose restart redis_cache`
2. اگر حل نشد: data loss قابل قبول است (FSM از بین می‌رود اما DB سالم است)
3. کاربران ممکن است نیاز به `/start` مجدد داشته باشند

---

## 📋 Checklist نهایی قبل از production

- [ ] ۵+ bot token در `BOT_SHARD_TOKENS` تنظیم شده
- [ ] SSL certificate معتبر (Let's Encrypt)
- [ ] MySQL primary + replica پیکربندی شده و replication فعال
- [ ] تمام migrationها اعمال شده (۰۰۱ تا ۰۰۵ — مخصوصاً ۰۰۵، بدونش ثبت‌نام و broadcast کرش می‌کنن؛ با `DESCRIBE users;` چک کنید ستون `shard_index` هست)
- [ ] seeders اجرا شده (`seed_tags.py`, `seed_gifts.py`, `seed_vip_plans.py`)
- [ ] webhooks برای همه shards ثبت شده
- [ ] `/health` جواب می‌دهد
- [ ] `/shards` تعداد shards را نشان می‌دهد
- [ ] `/metrics` Prometheus metrics برمی‌گرداند
- [ ] Backup روزیانه در crontab تنظیم شده
- [ ] Prometheus + Grafana قابل دسترسی
- [ ] تست پرداخت واقعی با مبلغ کم (۱۰۰۰ تومان)
- [ ] تست عملی: یه اکانت تازه کامل ثبت‌نام می‌کنه (نه فقط `/start` — تا انتهای پرسشنامه) و کرش نمی‌کنه
- [ ] تست عملی: ۱۰ کاربر همزمان `/start` می‌زنند و جواب می‌گیرند
- [ ] تست broadcast به ۱۰ کاربر
- [ ] Alerting rules در Prometheus فعال
- [ ] Disk space monitor (حداقل ۵۰GB free)
- [ ] Log rotation فعال (در docker-compose هست)

---

## 📞 پشتیبانی

اگر به مشکل خوردید:
1. اول logs را بررسی کنید: `docker compose logs <service>`
2. `/health` و `/metrics` را چک کنید
3. مطمئن شوید تمام containers `Up` هستند: `docker compose ps`
4. در صورت نیاز، backup را restore کنید

**موفق باشید! 🚀**
