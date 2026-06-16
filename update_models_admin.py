import re

file_path = 'database/models/models.py'

with open(file_path, 'r') as f:
    content = f.read()

# Check if is_banned is in models
if "is_banned: Mapped[bool]" not in content:
    # insert before is_online
    content = content.replace("is_online: Mapped[bool]", "is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)\n    is_online: Mapped[bool]")
    with open(file_path, 'w') as f:
        f.write(content)
    print("Added is_banned to models.py")

if "vip_expires_at: Mapped[Optional[datetime]]" not in content:
    content = content.replace("vip_quota: Mapped[int]", "vip_quota: Mapped[int] = mapped_column(Integer, default=0, nullable=False)\n    vip_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)")
    with open(file_path, 'w') as f:
        f.write(content)
    print("Added vip_expires_at to models.py")
