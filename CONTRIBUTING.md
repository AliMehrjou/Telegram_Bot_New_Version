[مشاهده نسخه فارسی ↓](#راهنمای-توسعه‌دهنده-ربات-مچینگ-تلگرام)

---

# Telegram Matching Bot — Developer Guide

> **Stack:** Python 3.11 · aiogram 3.x · FastAPI · SQLAlchemy 2.0 (Async) · MySQL 8 · Redis 7 · Docker Compose

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Repository Layout](#2-repository-layout)
3. [Architecture Deep-Dive](#3-architecture-deep-dive)
4. [Data Models](#4-data-models)
5. [Redis Key Schema](#5-redis-key-schema)
6. [FSM State Machine](#6-fsm-state-machine)
7. [Matching Engine](#7-matching-engine)
8. [Coin Economy](#8-coin-economy)
9. [Middleware Stack](#9-middleware-stack)
10. [Handler Catalogue](#10-handler-catalogue)
11. [Admin Panel](#11-admin-panel)
12. [Background Services](#12-background-services)
13. [Environment Variables](#13-environment-variables)
14. [Local Setup](#14-local-setup)
15. [Code Style Rules](#15-code-style-rules)
16. [Critical Bug Patterns](#16-critical-bug-patterns)
17. [Pull Request Workflow](#17-pull-request-workflow)

---

## 1. Project Overview

This repository powers a production-grade, fully asynchronous Telegram dating and matching bot targeting an Iranian user base. Its core capability is dynamically pairing two strangers for a **blind date** experience that progresses through three distinct phases:

```
[Queue] → [Matched + 5-second countdown] → [20-Question Questionnaire] → [Mutual Consent] → [Anonymous Chat]
```

Key distinguishing features:

| Feature | Implementation |
|---|---|
| Smart matchmaking | Redis atomic MULTI/EXEC transactions |
| Synchronized questionnaire | Redis `SADD` / `SCARD` per-question sync locks |
| Coin economy | `process_coin_transaction()` with event multipliers |
| Anonymous routing | Content filters: username, URL, phone regex |
| Privacy safety | BlockList enforced at queue-pop time via `SISMEMBER` |
| VIP system | Age/interest filtering + invisible mode + re-match |
| Background safety | 3-minute inactivity timeout via `DatingScheduler` |

---

## 2. Repository Layout

```
matching_bot_project/
├── api/
│   ├── main.py                  # FastAPI app + lifespan (startup/teardown)
│   └── routes/
│       ├── admin.py             # REST admin endpoints (stats, ban, coins)
│       └── webhook.py           # Telegram webhook receiver
├── bot/
│   ├── core/
│   │   ├── config.py            # Pydantic Settings (reads .env)
│   │   ├── constants.py         # ReplyBtn / InlineBtn / SystemMsg
│   │   ├── formatters.py        # build_unified_profile_card()
│   │   └── loader.py            # Singleton: bot, dp, redis_client, matching_engine, dating_scheduler
│   ├── filters/
│   │   └── custom.py            # IsAdminFilter, IsVIPFilter, ChatActiveFilter
│   ├── handlers/
│   │   ├── admin.py             # Admin commands + daily report loop
│   │   ├── anonymous_chat.py    # Consent + live routing + termination
│   │   ├── discovery.py         # Swipe flow + filter wizard
│   │   ├── explore.py           # Search / Nearby callbacks
│   │   ├── gacha.py             # Loot-box / XP system
│   │   ├── interactions.py      # View profile, block, report, DM, social
│   │   ├── matching.py          # Queue entry + handle_successful_match()
│   │   ├── profile.py           # Profile view + silent mode + deletion
│   │   ├── profile_edit.py      # Bio / photo / voice / location / age edit
│   │   ├── questionnaire.py     # 20-question synchronized answer flow
│   │   ├── safety.py            # In-chat report with evidence forwarding
│   │   ├── start.py             # /start + onboarding FSM (4 steps)
│   │   ├── transfer.py          # Peer-to-peer coin transfer
│   │   └── vip.py               # VIP panel + viewers + re-match
│   ├── keyboards/
│   │   ├── inline.py            # All InlineKeyboardMarkup factories
│   │   └── reply.py             # All ReplyKeyboardMarkup factories
│   ├── middlewares/
│   │   ├── anti_spam.py         # ThrottlingMiddleware (Redis NX lock)
│   │   ├── database.py          # DbSessionMiddleware (injects db_session)
│   │   └── force_join.py        # ForceJoinMiddleware (channel subscription)
│   └── states/
│       └── states.py            # All FSM StatesGroup definitions
├── database/
│   ├── models/
│   │   └── models.py            # SQLAlchemy ORM models
│   ├── queries/
│   │   └── crud.py              # All async DB queries
│   └── session.py               # Engine + sessionmaker + get_db_session()
├── json_files/
│   ├── iran_data.json           # 31 provinces + 222 cities
│   ├── profile_template.json    # HTML profile card template
│   ├── help.json                # Help text
│   ├── help_admin.json          # Paginated admin help
│   └── rules.json               # Bot rules / terms
├── scripts/
│   └── mysql_backup.sh          # Daily cron backup script
├── services/
│   ├── broadcast_worker.py      # Async broadcast to user lists
│   ├── matching_engine.py       # Redis-powered MatchingEngine class
│   └── scheduler.py             # DatingScheduler + OnlineStatusWorker
├── run.py                       # Entry point (polling or webhook mode)
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## 3. Architecture Deep-Dive

### 3.1 Request Lifecycle

```
Telegram Server
      │
      ▼
  Webhook POST /v1/webhook  (FastAPI)
      │
      ▼
  ThrottlingMiddleware    ← outer; drops flood before session opens
      │
      ▼
  DbSessionMiddleware     ← injects AsyncSession; checks is_banned; updates is_online
      │
      ▼
  ForceJoinMiddleware     ← enforces channel subscription (skips if user is matched/chatting)
      │
      ▼
  aiogram Router Tree     ← FSM-state-aware handler dispatch
      │
      ▼
  Handler Logic           ← DB reads/writes, Redis ops, Telegram API calls
```

### 3.2 Startup Sequence (`api/main.py` lifespan)

1. `Base.metadata.create_all` — creates missing tables (does **not** migrate existing columns).
2. `seed_sixty_question_bank_if_empty` — seeds 80 questions on first boot.
3. `matching_engine.connect()` — opens Redis connection pool for the engine.
4. `dating_scheduler.start_polling()` — launches 15-second inactivity scan loop.
5. `OnlineStatusWorker.start_polling()` — marks stale users offline every 60 s.
6. `_daily_report_loop` — fires at 23:59 UTC daily.
7. Webhook set (production) or deleted (development).

### 3.3 Development vs Production Mode

The app detects its mode by inspecting `settings.BASE_URL`:

- **Production** (`*.com / *.ir / *.net / *.org`, not `yourdomain.com`) → webhook-only via Uvicorn.
- **Development** (anything else) → concurrent polling + Uvicorn via `asyncio.gather`.

---

## 4. Data Models

All models live in `database/models/models.py` and use **SQLAlchemy 2.0 Mapped syntax**.

### User (primary table)

| Column | Type | Notes |
|---|---|---|
| `tg_id` | BigInteger (unique, indexed) | Telegram user ID |
| `public_id` | String(20) | Displayed in profile as `/user_XXXXXX` |
| `gender` | String(10) | `"Male"` or `"Female"` (capital first letter — match exactly) |
| `province` / `city` | String(100) | From `iran_data.json` |
| `coin_balance` | Integer | Current spendable coins |
| `total_earned_coins` | Integer | Lifetime earned |
| `total_spent_coins` | Integer | Lifetime spent |
| `is_vip` | Boolean | Set by admin or purchase |
| `vip_expires_at` | DateTime | Checked alongside `is_vip` |
| `invisible_mode` | Boolean | VIP-only; hides from search & queue |
| `silent_until` | DateTime | Blocks DMs and match requests until timestamp |
| `xp_points` / `level` / `lootbox_count` | Integer | Gacha system |
| `referrer_id` | FK → `users.id` | Internal row ID (not tg_id) |

> ⚠️ `referrer_id` references `users.id` (auto-increment primary key), **not** `users.tg_id`. Do not confuse them.

### MatchHistory

Tracks every pairing attempt. Key boolean flow:

```
is_active=True
  └─► user_one_approved + user_two_approved → True
        └─► chat_approved = True → anonymous chat opens
  └─► questionnaire_completed = True (set after all 20 answers)
ended_at set when is_active → False
```

### CoinTransaction

Every coin movement — positive (earned) or negative (spent) — must be logged here via `process_coin_transaction()`. **Never** mutate `coin_balance` directly.

### Other tables

`UserLike`, `UserReport`, `UserAnswer`, `Question`, `FriendList`, `BlockList` — see `models.py` for full column definitions.

### ⚠️ Schema Migration Rule

`create_all()` **never alters existing columns**. Any new column added to a model after the first deployment requires an explicit Alembic migration:

```bash
alembic revision --autogenerate -m "add_column_foo"
alembic upgrade head
```

---

## 5. Redis Key Schema

| Key Pattern | Type | TTL | Purpose |
|---|---|---|---|
| `user:state:{tg_id}` | Hash | 1 h | Live queue / match state (`status`, `gender`, `matched_with`, …) |
| `user:{id}:blocks` | Set | permanent | IDs this user has blocked |
| `user:{tg_id}:viewers` | ZSet | 7 days | VIP profile viewer log (score = Unix timestamp) |
| `user:{tg_id}:last_match_partner` | String | 24 h | Used by VIP re-match |
| `match:questions:{match_id}` | String | 1 h | Comma-separated question IDs for this match |
| `match:current_q_index:{match_id}` | String | 1 h | Deprecated; index stored in FSM |
| `match:{match_id}:q:{q_id}:sync` | Set | 1 h | Sync lock — contains tg_ids of answerers |
| `date:timeout:{match_id}` | Hash | 5 min (rolling) | Inactivity tracker (`last_activity`, `user_one_id`, `user_two_id`) |
| `throttling:{uid}:{type}` | String | 0.6 s | Anti-spam lock |
| `user:force_join:{uid}:v{ver}` | String | 5 min | Channel subscription cache |
| `bot:sponsors` | Hash | permanent | `channel_id → invite_link` (admin-managed) |
| `bot:sponsors_version` | String | permanent | Invalidates force_join cache on sponsor change |
| `bot:active_event_multiplier` | String | event TTL | Coin multiplier during active event |
| `user:{uid}:likes_today` | String | until midnight | Daily like counter |
| `user:blocks_today:{uid}` | String | until midnight | Daily block counter (3 blocks → 24 h cooldown) |
| `user:block_cooldown:{uid}` | String | 24 h | Matchmaking ban after excessive blocking |
| `pending_ref:{uid}` | String | 1 h | Referral ID captured before force_join resolves |

---

## 6. FSM State Machine

All state groups are defined in `bot/states/states.py`.

```
/start
  └─► OnboardingStates.waiting_for_terms_acceptance
        └─► waiting_for_gender
              └─► waiting_for_age
                    └─► waiting_for_province
                          └─► waiting_for_city
                                └─► [clear → main menu]

match_* callback
  └─► (VIP) VIPStates.waiting_for_age_filter
  └─► MatchingStates.waiting_in_queue
        └─► [match found] QuestionnaireStates.waiting_for_questions_to_start
              └─► (5 s sleep) answering_questions  ←──────────────────────┐
                    └─► waiting_for_partner_answer                         │
                          └─► [both answered] → answering_questions (next) ┘
                                └─► [20 done] ChatStates.waiting_for_approval
                                      └─► [both approved] anonymous_chat_active
                                            └─► [end_active_chat] → clear

ProfileEditStates.*       — bio / photo / voice / location / age flows
DiscoveryStates.*         — swipe + filter wizard
ReportStates.*            — in-chat & profile report flows
ChatStates.typing_direct_message  — DM composition
AdminStates.*             — support reply + broadcast
EventStates.*             — event creation wizard
PBroadcastStates.*        — personalized broadcast wizard
CoinTransferStates.*      — peer-to-peer coin transfer
```

---

## 7. Matching Engine

`services/matching_engine.py` — `MatchingEngine` class.

### Queue Key Naming

```
match:queue:random                    ← random match (any gender)
match:queue:want_{target}:{gender}    ← gender-targeted match
match:queue:province:{normalized}     ← same-province match
```

**Symmetry proof:** if User A (female, wants male) waits at `want_male:female`, User B (male, wants female) pops from `want_male:female` — the keys are identical. This is mathematically correct.

### `find_match()` Algorithm

1. Peek up to 15 candidates from the tail of the target queue.
2. For each candidate, check bilateral age constraints (both directions must pass).
3. For VIP callers with interests, skip candidates with zero intersection.
4. Check BlockList via `SISMEMBER` in both directions.
5. `WATCH` the candidate's state key → `MULTI` → set both users' `status = "matched"` → `EXEC`.
6. On `WatchError` (race condition), push the candidate back and retry.
7. If no valid candidate found after all attempts, call `add_to_queue()` and return `None`.

### Coin Settlement

Coins are deducted **only after a confirmed match**, never speculatively. The caller's cost is always deducted; the partner pays 1 coin if they can afford it, but the match proceeds either way.

---

## 8. Coin Economy

### The Only Valid Way to Modify Coins

```python
await crud.process_coin_transaction(session, user, amount, "description")
```

This function:
- Checks sufficient balance for negative amounts.
- Applies active event multiplier (from Redis) to positive amounts.
- Updates `coin_balance`, `total_earned_coins`, `total_spent_coins`.
- Inserts a `CoinTransaction` log row.

**Never do this:**
```python
user.coin_balance -= 1          # ❌ bypasses audit log and multiplier
user.total_spent_coins += 1     # ❌
await db_session.commit()
```

The only documented exception is coin settlement inside `_settle_coins_after_match()` in `matching.py`, which uses direct mutation intentionally with its own comment. All other locations must use `process_coin_transaction`.

### Event Multiplier

Admin runs `/event_create` → wizard → stores multiplier in Redis with TTL:

```redis
SET bot:active_event_multiplier 2.5 EX 7200
```

`process_coin_transaction` reads this on every positive transaction automatically.

---

## 9. Middleware Stack

Order is critical. Registered in `run.py` → `register_bot_middlewares_and_routers()`:

```
outer:  ThrottlingMiddleware    (rate limit: 1 event per 0.6 s per user per type)
inner:  DbSessionMiddleware     (injects db_session; updates online status; blocks banned users)
inner:  ForceJoinMiddleware     (checks channel subscription; bypasses if matched/chatting)
```

`outer_middleware` runs **before** session creation. This is intentional — we reject floods before paying the cost of opening a DB connection.

### DbSessionMiddleware Behaviour

- Opens an `AsyncSession` and injects it as `data["db_session"]`.
- If the user `is_banned`, returns `None` immediately (swallows the update).
- Updates `is_online = True` and `last_active` using a Redis TTL key to avoid hammering MySQL on every message.
- Does **not** commit — handlers are responsible for their own commits.

---

## 10. Handler Catalogue

### `start.py`

- `/start` with `ref_XXXXX` arg → referral chain detection.
- 4-step onboarding FSM: terms → gender → age → province → city.
- Main menu button dispatchers (`ReplyBtn.*`).
- Support ticket submission to admins.

### `matching.py`

- `match_*` callbacks → `enter_match_queue()`.
- VIP age filter interception → `process_vip_age_filter()`.
- `handle_successful_match()` — must be called once per confirmed match; manages the entire 5-second countdown + question delivery.

### `questionnaire.py`

- `ans_a_{qid}` / `ans_b_{qid}` callbacks.
- Anti-double-tap via immediate state flip to `waiting_for_partner_answer`.
- Redis `SADD` sync — only the coroutine where `SCARD == 2` drives advancement.
- `finalize_questionnaire_and_request_approval()` — scores compatibility, dispatches consent prompt.

### `anonymous_chat.py`

- `approve_chat_yes/no` → `register_chat_consent()`.
- `route_anonymous_chat_message()` — text filter + `copy_message` for media.
- `end_active_anonymous_chat()` — cleans Redis, FSM, DB in one operation.

### `interactions.py`

- Profile view, block/unblock, like, add/remove friend, report.
- DM request with 1-coin deduction (uses direct mutation — known exception).
- `execute_chat_termination()` — shared helper used by safety.py and interactions.py.
- `execute_user_blocking()` — shared helper; syncs BlockList to Redis.

### `admin.py`

- `/addcoins`, `/removecoins`, `/banuser`, `/setvip`, `/resetprofile`, `/userinfo`.
- `/broadcast` — copies any media message to all users.
- `/pbroadcast` — personalized broadcast with `{name}`, `{city}`, `{coins}`, `{age}` variables.
- `/event_create` → `EventStates` wizard → stores Redis multiplier with TTL.
- `/report`, `/report_auto`, `_daily_report_loop`.
- Dynamic sponsor management: `/addsponsor`, `/removesponsor`, `/sponsors`.

---

## 11. Admin Panel

### Telegram Commands (admin-only, gated by `IsAdminFilter`)

| Command | Effect |
|---|---|
| `/addcoins <id> <n>` | Add coins to user |
| `/removecoins <id> <n>` | Deduct coins |
| `/addcoinsall <n>` | Give coins to every user |
| `/addcoinsvip <n>` | Give coins to VIP users only |
| `/banuser <id>` | Instant ban (cannot ban other admins) |
| `/unbanuser <id>` | Lift ban |
| `/setvip <id> <days>` | Grant VIP for N days |
| `/resetprofile <id>` | Wipe gender/age/province; set `completed_registration=False` |
| `/userinfo <id>` | Full user profile + match/chat stats |
| `/adminstats` | Interactive stats dashboard |
| `/broadcast` | Send any message to all users |
| `/pbroadcast` | Filtered personalized broadcast |
| `/event_create` | Launch coin multiplier event |
| `/event_list` | List active events |
| `/event_end` | End active event (clears Redis key) |
| `/report` | Instant daily report |
| `/report_auto` | Toggle auto-report at 23:59 UTC |
| `/addsponsor <id> <link>` | Add channel to force-join list |
| `/removesponsor <id>` | Remove channel |
| `/sponsors` | List all sponsor channels |
| `/help_admin` | Paginated admin guide (3 pages) |

### REST API (`api/routes/admin.py`)

Requires `X-Api-Key: {ADMIN_SECRET_TOKEN}` header.

| Endpoint | Method | Description |
|---|---|---|
| `/admin/stats` | GET | High-level bot statistics |
| `/admin/stats/advanced` | GET | Today's registrations, top provinces, conversion rate |
| `/admin/user/{tg_id}` | GET | Full user profile |
| `/admin/coins/add` | POST | Add coins via API |
| `/admin/ban` | POST | Ban / unban via API |
| `/admin/broadcast` | POST | Trigger broadcast via API |

---

## 12. Background Services

### DatingScheduler (`services/scheduler.py`)

- Registered at lifespan startup via `dating_scheduler.start_polling()`.
- Scans `date:timeout:*` keys every 15 seconds using `scan_iter`.
- If `last_activity` is older than 180 seconds (3 minutes):
  - Marks `MatchHistory.is_active = False`.
  - Deletes Redis state keys for both users.
  - Clears FSM for both users.
  - Sends timeout notification.
- Uses `asyncio.create_task` per expired key to avoid blocking the scan loop.

### OnlineStatusWorker

- Runs every 60 seconds.
- Bulk-updates `is_online = False` for all users whose `last_active < (now - 5 minutes)`.
- Uses a single `UPDATE ... WHERE` statement — never loads user objects.

### _daily_report_loop

- Sleeps until 23:59 UTC, then calls `send_daily_reports()`.
- Only sends to admins who have enabled auto-reports via `/report_auto`.

---

## 13. Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | ✅ | Telegram bot token from BotFather |
| `REQUIRED_CHANNEL_ID` | ✅ | Default force-join channel ID (negative integer) |
| `CHANNEL_INVITE_LINK` | ✅ | Invite link for the default channel |
| `DATABASE_URL` | ✅ | `mysql+aiomysql://user:pass@host:port/dbname` |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | ✅ | Individual DB params |
| `DB_ROOT_PASSWORD` | ✅ | MySQL root password (Docker Compose) |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | ✅ | Redis connection params |
| `ADMIN_USER_IDS` | ✅ | Comma-separated Telegram IDs of admins |
| `ADMIN_SECRET_TOKEN` | ✅ | Shared secret for webhook verification + REST API |
| `BASE_URL` | ✅ | Public HTTPS URL (production) or empty (dev polling) |
| `WEBHOOK_PATH` | — | Default: `/api/v1/webhook` |
| `PORT` | — | Default: `8000` |
| `HOST` | — | Default: `0.0.0.0` |
| `BOT_USERNAME` | — | e.g. `@YourBotUsername` |
| `SUPPORT_USERNAME` | — | Support contact handle |
| `PROXY_URL` | — | Optional SOCKS5/HTTP proxy for Telegram connection |

> ⚠️ **Security:** never commit real credentials. Revoke any token pushed to version control immediately via BotFather. The `.env` file is listed in `.gitignore`.

---

## 14. Local Setup

```bash
# 1. Clone
git clone https://github.com/your-repo/matching_bot_project.git
cd matching_bot_project

# 2. Configure
cp .env.example .env
# Edit .env: set BOT_TOKEN, ADMIN_USER_IDS, ADMIN_SECRET_TOKEN at minimum

# 3. Start services
docker-compose up -d --build

# 4. Verify
docker-compose logs -f bot
```

In development mode (`BASE_URL` empty or `yourdomain.com`), the bot uses **long polling** automatically — no SSL or domain required.

---

## 15. Code Style Rules

### Async-First

```python
# ✅ correct
await asyncio.sleep(5)
result = await session.execute(stmt)

# ❌ never
time.sleep(5)
session.execute(stmt)
```

### All DB Operations Must Use AsyncSession

```python
async def my_handler(message: Message, db_session: AsyncSession) -> None:
    user = await crud.get_user_by_tg_id(db_session, message.from_user.id)
    ...
    await db_session.commit()
```

### Wrap Every Telegram API Call

```python
try:
    await bot.send_message(chat_id=uid, text="...")
except TelegramForbiddenError:
    logger.warning("User %s blocked the bot", uid)
except TelegramAPIError as exc:
    logger.error("Telegram API error for user %s: %s", uid, exc)
```

### Button Label Consistency

Every `F.text == "..."` filter must use the exact same string as the `KeyboardButton(text=...)` definition. **Always source both from `ReplyBtn` constants:**

```python
# bot/keyboards/reply.py
KeyboardButton(text=ReplyBtn.MY_PROFILE)

# bot/handlers/profile.py
@router.message(F.text == ReplyBtn.MY_PROFILE)
async def view_user_profile(...):
```

### Router Registration

Every new router **must** be imported and included in `run.py`:

```python
from matching_bot_project.bot.handlers import my_new_feature

# inside register_bot_middlewares_and_routers():
dp.include_router(my_new_feature.router)
```

Omitting this silently discards all messages handled by that router — no error is raised.

### Cross-User FSM

```python
# To read/write another user's FSM state:
ctx = FSMContext(
    storage=dp.storage,
    key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id)
)
await ctx.set_state(SomeState.some_value)
await ctx.update_data(key=value)
```

### New Columns Require Migrations

```python
# Adding this to models.py is NOT enough in production:
new_field: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

# You must also generate and run an Alembic migration.
```

---

## 16. Critical Bug Patterns

These are recurring mistakes that have caused production issues. Read them before touching any handler.

| Pattern | Wrong | Right |
|---|---|---|
| **Coin mutation** | `user.coin_balance -= 1` | `await process_coin_transaction(session, user, -1, "reason")` |
| **Button label mismatch** | `F.text == "پروفایل"` (typo) | `F.text == ReplyBtn.MY_PROFILE` |
| **Forgot router registration** | New file never included in `run.py` | Always add `dp.include_router(handler.router)` |
| **Wrong matching_engine import** | `from services import matching_engine` (module) | `from bot.core.loader import matching_engine` (singleton instance) |
| **Broad exception handling** | `except Exception` on Telegram calls | Catch `TelegramForbiddenError` and `TelegramAPIError` specifically |
| **Timezone-naive datetime** | `datetime.utcnow()` for comparisons with `silent_until` | `datetime.now(timezone.utc).replace(tzinfo=None)` (consistent with stored values) |
| **self-match** | User matched with their own tg_id | Always check `if matched_partner_id == tg_id` after `find_match()` |
| **Schema drift** | Relying on `create_all()` to add columns | Always write Alembic migrations |
| **Redis key leak** | Sync keys never cleared on abandoned match | Safety TTL (`_SYNC_KEY_TTL_SECONDS = 3600`) is set on first answer |
| **Double approval race** | Two concurrent `approve_chat_yes` coroutines both activate chat | Guard: `if match_history.chat_approved: return` before any write |

---

## 17. Pull Request Workflow

1. **Branch naming:** `feature/short-description` or `bugfix/what-is-fixed`.
2. **Scope:** one logical change per PR — avoid mixing features and refactors.
3. **Before opening a PR, verify:**
   - [ ] New router registered in `run.py`.
   - [ ] No hardcoded button strings — all labels come from `bot/core/constants.py`.
   - [ ] All coin mutations routed through `process_coin_transaction()`.
   - [ ] All Telegram API calls wrapped in `try/except TelegramForbiddenError, TelegramAPIError`.
   - [ ] No `time.sleep()` anywhere — only `await asyncio.sleep()`.
   - [ ] New DB columns have a corresponding Alembic migration.
   - [ ] `.env` contains no real secrets in any committed file.
4. **Commit messages:** use the imperative form and explain *why*, not just *what*. Example: `fix: route coin deduction through process_coin_transaction in matching.py to restore audit log`.
5. **Testing:** run the bot locally with Docker Compose and exercise the changed flow end-to-end before opening the PR.