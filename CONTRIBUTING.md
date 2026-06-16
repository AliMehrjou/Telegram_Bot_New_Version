[مشاهده نسخه فارسی (CONTRIBUTING.fa.md)](./CONTRIBUTING.fa.md)

# Contributing to the Telegram Dating Bot

Thank you for contributing! This document provides guidelines and an overview of the architecture to help you understand how the bot operates, ensuring future developments remain stable, asynchronous, and scalable.

## 🏗 Architectural Overview

The bot operates on a fully asynchronous architecture designed to mitigate blocking calls and race conditions.

- **FastAPI + aiogram:** FastAPI handles incoming Webhook requests from Telegram and exposes an internal Admin API. Incoming updates are asynchronously fed to the `aiogram` Dispatcher (`dp`).
- **Middleware Hierarchy:** Middlewares execute sequentially to enforce global rules before a handler runs:
  1. `ThrottlingMiddleware`: Prevents spam.
  2. `DbSessionMiddleware`: Injects an active `AsyncSession` into handlers and dynamically updates the `is_online` status of the user.
  3. `ForceJoinMiddleware`: Enforces mandatory channel subscription. It smartly bypasses this check if the user is in an active Redis state (`matched` or `chatting`) to prevent freezing live sessions.

## 🗄 Database & Models (`models.py`)

We use **SQLAlchemy 2.0 (Async)**. All database tables and models reside in `database/models/models.py`.

### Best Practices for Models:
- **Updating Models:** When you add a new column (e.g., `city`, `tags`), always define it explicitly using SQLAlchemy 2.0's `Mapped[Type] = mapped_column(...)`.
- **Migrations:** We use Alembic. Whenever you update `models.py`, generate a new migration script and upgrade the database.
- **Relationships:** Maintain strict `ForeignKey` declarations with `ondelete="CASCADE"` or `SET NULL` depending on the data lifecycle to prevent orphaned records.

## 📝 Code Style & Standards

To maintain a healthy codebase, strictly adhere to the following rules:

1. **Fully Asynchronous Execution:**
   - **Never** use synchronous `time.sleep()`. Always use `await asyncio.sleep()`.
   - All DB operations must use `AsyncSession` and `await session.execute(...)`.

2. **Safe Economy (Atomic Updates):**
   - Transactions modifying `coin_balance`, `total_earned_coins`, or `total_spent_coins` must be committed cleanly.
   - For matchmaking, coin deduction is **deferred** until the match is actually confirmed to prevent ghost deductions.

3. **Robust Telegram API Handling:**
   - Any external API call (e.g., `bot.send_message`, `bot.copy_message`) can fail if a user deletes their account or blocks the bot.
   - **Always** wrap outbound API calls in `try/except` blocks handling `TelegramForbiddenError` and `TelegramAPIError` to prevent the event loop from crashing.

4. **Cross-User FSM Management:**
   - To read/write the state of a *partner* user, dynamically resolve their state:
     ```python
     ctx = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id))
     await ctx.clear()
     ```

## 🔄 Pull Request Workflow

1. **Branching:** Create a new branch for your feature or bug fix (e.g., `feature/add-gift-system` or `bugfix/fix-timeout`).
2. **Local Testing:** Ensure your `.env` is properly configured. Test the bot locally. Verify that database changes persist properly and no new crashes are introduced.
3. **Commit Messages:** Write descriptive commit messages summarizing the "Why" and "What" of your changes.
4. **Submit PR:** Open a Pull Request targeting the `main` branch. Provide a summary of the changes and tag relevant issues.
