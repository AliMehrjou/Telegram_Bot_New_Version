# PATCH NOTES — matching_bot_project

این سند خلاصه‌ای از پچ‌های اعمال‌شده برای رفع باگ‌های **CRITICAL** و **HIGH** است. تمامی فایل‌های اصلاح‌شده در همین بسته قرار دارند و می‌توانید مستقیماً جایگزین نسخه‌های قبلی کنید.

## نحوه اعمال

1. یک نسخه پشتیبان از پروژه فعلی بگیرید.
2. فایل‌های این بسته را با حفظ ساختار پوشه‌ها در پروژه خود کپی کنید (جایگزین کنید).
3. دستور `python -m py_compile <file>` را برای هر فایل اجرا کنید تا از صحت syntax مطمئن شوید.
4. در فایل `.env` این متغیرها را اضافه کنید (اختیاری ولی توصیه می‌شود):
   ```env
   SYSTEM_GUARD_SECRET_HASH=<sha256 hash of your sys_diag password>
   ```
5. اگر از PostgreSQL استفاده می‌کنید، مطمئن شوید همه ستون‌های `DateTime` از نوع `DateTime(timezone=True)` هستند.

## فایل‌های اصلاح‌شده

### فایل‌های هسته و دیتابیس
- `database/session.py` — CRIT-01: اصلاح درایور پیش‌فرض به `asyncmy`.
- `database/queries/crud.py` — HIGH-02, HIGH-03, HIGH-04, M-01.
- `bot/core/config.py` — HIGH-01: حذف property تکراری `parsed_admin_ids`.

### Middlewares و Filters
- `bot/middlewares/database.py` — CRIT-09, M-26.
- `bot/middlewares/system_guard.py` — CRIT-10, HIGH-17, L-16, L-17.
- `bot/middlewares/force_join.py` — HIGH-18, HIGH-19, HIGH-20, L-18.
- `bot/filters/custom.py` — HIGH-21, HIGH-22, M-19, M-20.

### API
- `api/main.py` — HIGH-23, HIGH-24, M-21, M-22, L-20, L-25.
- `api/routes/admin.py` — CRIT-11, HIGH-25, HIGH-26.
- `api/routes/payment.py` — CRIT-02, CRIT-12, HIGH-15.
- `api/routes/webhook.py` — M-17.

### Handlers
- `bot/handlers/profile.py` — CRIT-03.
- `bot/handlers/matching.py` — CRIT-13, HIGH-09, M-14.
- `bot/handlers/interactions.py` — CRIT-05, HIGH-16, HIGH-34.
- `bot/handlers/payments.py` — CRIT-06, CRIT-07, CRIT-12, int-parse wraps.
- `bot/handlers/admin.py` — CRIT-08, HIGH-30, HIGH-34, HIGH-35, HIGH-38, M-28.
- `bot/handlers/questionnaire.py` — HIGH-06, HIGH-07, HIGH-08.
- `bot/handlers/start.py` — HIGH-10.
- `bot/handlers/anonymous_chat.py` — HIGH-11, L-11, L-14.
- `bot/handlers/profile_edit.py` — HIGH-13.
- `bot/handlers/vip.py` — HIGH-27, HIGH-28, HIGH-29, M-01.
- `bot/handlers/gacha.py` — HIGH-31.
- `bot/handlers/discovery.py` — HIGH-32.
- `bot/handlers/safety.py` — HIGH-33.
- `bot/handlers/transfer.py` — HIGH-36.

### Services
- `services/matching_engine.py` — HIGH-14, M-02, M-03, M-06, L-13.
- `services/scheduler.py` — HIGH-10, HIGH-15, HIGH-23.
- `services/reengagement.py` — HIGH-16, M-06.
- `services/zarinpal.py` — CRIT-02, M-07, M-08, HIGH-17, L-29.
- `services/payment_settings.py` — M-09, L-26, L-27.
- `services/broadcast_worker.py` — M-05, M-12, L-30, L-33.

### اجرای اصلی
- `run.py` — HIGH-05, L-07.

## نکات مهم

### 1. UniqueConstraint روی MatchHistory (CRIT-04)
برای جلوگیری کامل از مچ تکراری، علاوه بر پچ کد، یک `UniqueConstraint` در `database/models/models.py` روی `(user_one_id, user_two_id, is_active)` اضافه کنید و `IntegrityError` را در `create_match_history` بگیرید.

### 2. پاکسازی داده‌های قدیمی VIP
```sql
UPDATE users SET is_vip = 0
WHERE is_vip = 1 AND vip_expires_at IS NOT NULL AND vip_expires_at < UTC_TIMESTAMP();
```

### 3. متغیرهای محیطی جدید
- `SYSTEM_GUARD_SECRET_HASH` (اختیاری): هش SHA-256 پسورد `/sys_diag`.

### 4. تست پس از اعمال
- اجرای `/start` با کاربر جدید.
- خرید بسته سکه با درگاه زرین‌پال (CRIT-02).
- `/addcoinsall` (CRIT-08).
- حذف اکانت (CRIT-03).
- `/vip_rematch` (HIGH-27/28/29).

## باگ‌های باقی‌مانده
باگ‌های MEDIUM و LOW در این پچ گنجانده نشده‌اند چون تأثیر عملیاتی کمی دارند. اگر خواستید، می‌توانم در یک پچ بعدی آنها را هم اعمال کنم.
