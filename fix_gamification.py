# Remove gamification
import os

if os.path.exists("bot/handlers/gamification.py"):
    os.remove("bot/handlers/gamification.py")

with open("run.py", "r") as f:
    c = f.read()

c = c.replace(", gamification", "")
c = c.replace("    dp.include_router(gamification.router)\n", "")

with open("run.py", "w") as f:
    f.write(c)

with open("database/models/models.py", "r") as f:
    c = f.read()

# We will just leave UserBadge model to not break database, it doesn't hurt.
# Or remove it
import re
c = re.sub(r'class UserBadge\(Base\):.*?awarded_at: Mapped\[datetime\] = mapped_column\(DateTime, default=datetime\.utcnow\)', '', c, flags=re.DOTALL)
with open("database/models/models.py", "w") as f:
    f.write(c)
