import re

file_path = 'bot/middlewares/database.py'

with open(file_path, 'r') as f:
    content = f.read()

# Add a check for user.is_banned
ban_check = """
        async with async_session_factory() as session:
            # Check for ban status if we can
            user_id = event.from_user.id if event.from_user else None
            if user_id:
                from database.queries import crud
                user = await crud.get_user_by_tg_id(session, user_id)
                if user and getattr(user, 'is_banned', False):
                    # Silently reject
                    if isinstance(event, CallbackQuery):
                        await event.answer("حساب شما مسدود شده است.", show_alert=True)
                    return None

            data["db_session"] = session
            return await handler(event, data)
"""

content = re.sub(r'async with async_session_factory\(\) as session:\s+data\["db_session"\] = session\s+return await handler\(event, data\)', ban_check.strip(), content)

with open(file_path, 'w') as f:
    f.write(content)
print("Added ban check to database.py")
