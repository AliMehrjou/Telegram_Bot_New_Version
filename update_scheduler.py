import re

file_path = 'services/scheduler.py'

with open(file_path, 'r') as f:
    content = f.read()

vip_check_method = """
    async def check_vip_expiry(self):
        \"\"\"Checks and revokes expired VIPs\"\"\"
        from sqlalchemy import select, update, true
        from database.models.models import User

        while True:
            try:
                now_utc = datetime.utcnow()
                async with self.session_factory() as session:
                    # Find users whose vip_expires_at is in the past and are currently VIP
                    stmt = select(User.tg_id).where(
                        (User.is_vip == true()) &
                        (User.vip_expires_at != None) &
                        (User.vip_expires_at < now_utc)
                    )
                    result = await session.execute(stmt)
                    expired_users = [row[0] for row in result.all()]

                    if expired_users:
                        # Revoke VIP
                        await session.execute(
                            update(User)
                            .where(User.tg_id.in_(expired_users))
                            .values(is_vip=False, vip_expires_at=None)
                        )
                        await session.commit()

                        # Notify users
                        for tg_id in expired_users:
                            try:
                                await self.bot.send_message(
                                    chat_id=tg_id,
                                    text="⚠️ اشتراک VIP شما به پایان رسید."
                                )
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"Error checking VIP expiry: {e}")

            await asyncio.sleep(3600)  # Check every hour
"""

if "check_vip_expiry" not in content:
    # insert before verify_timeout_loops
    content = content.replace("async def verify_timeout_loops(self):", vip_check_method + "\n    async def verify_timeout_loops(self):")

    # modify start_polling to also run check_vip_expiry
    content = content.replace("self._running_task = asyncio.create_task(self.verify_timeout_loops())", "self._running_task = asyncio.create_task(self.verify_timeout_loops())\n            asyncio.create_task(self.check_vip_expiry())")

    with open(file_path, 'w') as f:
        f.write(content)
    print("Added check_vip_expiry to scheduler.py")
