import re

file_path = 'services/scheduler.py'

with open(file_path, 'r') as f:
    content = f.read()

notifications_method = """
    async def run_notifications(self):
        \"\"\"Runs smart notifications periodically\"\"\"
        from services.notification_service import NotificationService
        ns = NotificationService(self.bot, self.session_factory, self.redis)

        while True:
            try:
                await ns.run_all()
            except Exception as e:
                logger.error(f"Error running notifications: {e}")

            await asyncio.sleep(3600)  # Check every hour
"""

if "run_notifications" not in content:
    # insert before verify_timeout_loops
    content = content.replace("async def verify_timeout_loops(self):", notifications_method + "\n    async def verify_timeout_loops(self):")

    # modify start_polling to also run run_notifications
    content = content.replace("asyncio.create_task(self.check_vip_expiry())", "asyncio.create_task(self.check_vip_expiry())\n            asyncio.create_task(self.run_notifications())")

    with open(file_path, 'w') as f:
        f.write(content)
    print("Added run_notifications to scheduler.py")
