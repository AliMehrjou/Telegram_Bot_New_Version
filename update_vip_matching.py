import re

file_path = 'bot/handlers/matching.py'

with open(file_path, 'r') as f:
    content = f.read()

# Add inline buttons for age range selection before entering queue for VIP users
new_queue_logic = """
    # ── 4. Coin balance check ────────────────────────────────────────────────
    if user.coin_balance < cost:
        await call.answer(
            "❌ سکه‌های شما کافی نیست! برای دریافت سکه از منوی اصلی اقدام کنید.",
            show_alert=True,
        )
        return

    # VIP Age filter handling
    if getattr(user, 'is_vip', False) and not call.data.startswith("match_vip_age_"):
        # We need to ask for age
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="۱۸-۲۵", callback_data=f"match_vip_age_18_25_{match_type}"),
                InlineKeyboardButton(text="۲۵-۳۰", callback_data=f"match_vip_age_25_30_{match_type}")
            ],
            [
                InlineKeyboardButton(text="۳۰-۴۰", callback_data=f"match_vip_age_30_40_{match_type}"),
                InlineKeyboardButton(text="هر سنی", callback_data=f"match_vip_age_all_all_{match_type}")
            ],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_queue")]
        ])
        await call.message.edit_text("لطفاً بازه سنی مورد نظر پارتنر خود را انتخاب کنید:", reply_markup=kb)
        await call.answer()
        return

    min_age = None
    max_age = None
    if call.data.startswith("match_vip_age_"):
        parts = call.data.split("_")
        # match_vip_age_18_25_type
        if parts[3] != "all":
            min_age = int(parts[3])
            max_age = int(parts[4])
"""

content = re.sub(r'# ── 4\. Coin balance check ────────────────────────────────────────────────.*?(?=# ── 5\. Lock state)', new_queue_logic.strip() + '\n\n    ', content, flags=re.DOTALL)

engine_call = """
    # ── 8. Invoke the matching engine ────────────────────────────────────────
    matched_partner_id: int | None = await matching_engine.find_match(
        tg_id=tg_id,
        gender=user.gender,
        target_gender=target_gender,
        province=province,
        interests=user.interests,
        min_age=min_age,
        max_age=max_age,
        caller_age=user.age
    )
"""
content = re.sub(r'# ── 8\. Invoke the matching engine ────────────────────────────────────────.*?(?=# ── 9\. Ghost-match guard)', engine_call.strip() + '\n\n    ', content, flags=re.DOTALL)

with open(file_path, 'w') as f:
    f.write(content)
print("Added VIP age filtering to matching.py")
