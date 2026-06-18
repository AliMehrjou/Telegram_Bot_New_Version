# Code Review Report

## 1. Circular Imports & Scope Issues
- **`bot/handlers/start.py`**: The file header explicitly states `waiting_for_province` needs to be added to `OnboardingStates`. In `bot/handlers/start.py` near line 450, there's a `try/except TypeError` around `crud.complete_user_registration` waiting for the signature update to include `province` and `city` correctly.
- **`bot/handlers/interactions.py`**: It implements local definitions or helper functions that might create dependencies or conflict with `matching.py` regarding FSM clearing.

## 2. API/Middleware Issues
- **FastAPI Admin Routes (`api/routes/admin.py`, `api/routes/webhook.py`)**: `ADMIN_SECRET_TOKEN` is incorrectly validated as a plain string equal to `settings.ADMIN_SECRET_TOKEN` (e.g. `x_api_key != settings.ADMIN_SECRET_TOKEN`).
- **`bot/middlewares/database.py`**: `is_banned` is intercepted correctly immediately after fetching the user session! However, it logs the ban but returns `None` which may swallow updates or unhandled callback query loaders. This is generally acceptable in Aiogram but could be improved.

## 3. Redis Sets & BlockList logic
- `BlockList` tracking uses MySQL but atomic engine rules rely on Redis `user:{id}:blocks`.
- Blocking a user in `interactions.py` uses `await redis_client.sadd(f"user:{blocker_id}:blocks", str(blocked_id))`.
- When matching (`matching_engine.py`), it correctly checks `sismember`.

## 4. VIP & Invisible Mode
- Invisible mode works for VIPs in explore/SQL queries. The user can still queue.
- `vip_expires_at` is heavily checked `if user.is_vip or (user.vip_expires_at and user.vip_expires_at > datetime.utcnow())`.

## 5. Coin Transactions
- Coin operations route correctly through `process_coin_transaction` in most `crud` and admin routes.
- **Critical Bug**: In `bot/handlers/interactions.py` (`request_direct_message` around line 450), the coin deduction bypasses `process_coin_transaction`. It does `caller.coin_balance -= 1` and `db_session.commit()` directly, violating the constraint: "All coin operations must be routed through `process_coin_transaction()` to maintain an accurate audit log."
- **Critical Bug**: In `bot/handlers/matching.py` (around line 270 and 366), coin deduction for successful matches *also* directly subtracts `user.coin_balance -= cost` without using `process_coin_transaction()`, violating the audit log constraint!

## 6. Profile View Tracking
- Viewers tracking is incorrectly restricted. `user:{viewed_id}:viewers` logic exists in `bot/handlers/interactions.py` using Unix timestamp and 7-day TTL (`expire`), but the instruction "Profile views for VIP users are logged in Redis using a Sorted Set (`ZADD`) at `user:{viewed_id}:viewers`" is generally satisfied.

## 7. Memory Constraints Violations Found:
- **"All coin operations must be routed through `process_coin_transaction()`..."**: Violated in `bot/handlers/interactions.py` (DM coin deduction) and `bot/handlers/matching.py` (Match coin deduction).
- **"Always use the singleton `MatchingEngine` instance directly from `bot.core.loader`... instead of manually re-instantiating it"**: Verified `from matching_bot_project.bot.core.loader import matching_engine` is used everywhere except `bot/handlers/start.py` line 119 (`from matching_bot_project.services import matching_engine`), which then uses `matching_engine.remove_from_queue`. This might refer to the module itself or the instance. Wait, `matching_engine` in `services` is the file. `from matching_bot_project.services import matching_engine` imports the module, not the instance. It then incorrectly calls `await matching_engine.remove_from_queue(...)` which will fail because `remove_from_queue` is an instance method of `MatchingEngine` class.
- **"All Telegram API calls must be wrapped in try/except blocks catching TelegramForbiddenError and TelegramAPIError"**: Need to check if `interactions.py` DM and `explore.py` wraps them perfectly. In `interactions.py` line 480 `bot.send_message` uses `except Exception as exc:`, not specific Telegram API errors.

## 8. Missing FSM State Definition
- In `bot/states/states.py`, `waiting_for_province` is supposedly missing as per the warning docstring in `bot/handlers/start.py`.
- `database/models/models.py` lacks `province`, `coin_balance`, `total_earned_coins`, and `total_spent_coins` if not already updated. (Actually, `models.py` has `province`, `coin_balance`! The docstring in `start.py` is outdated but logic in `start.py` assumes `crud.complete_user_registration` might raise `TypeError`).

## 9. Gamification Constraints
- "Do not implement gamification features for this project." - Memory constraint.
- The README explicitly mentions "Gamified Compatibility Questionnaire". Wait, the instruction says "Do not implement gamification features". So the existing features are okay or need to be stripped? User only asked for a report.

## Summary of Main Issues:
1. **Coin Economy Audit Log bypass**: `interactions.py` (DM) and `matching.py` manual deductions.
2. **Incorrect instance import**: `bot/handlers/start.py` uses `from matching_bot_project.services import matching_engine` (module) instead of the loader singleton.
3. **General Exception Handling**: `bot.send_message` wrapped in broad `except Exception` instead of specific `TelegramForbiddenError` / `TelegramAPIError`.
