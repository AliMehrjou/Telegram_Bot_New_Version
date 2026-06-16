import re

file_path = 'database/models/models.py'

with open(file_path, 'r') as f:
    content = f.read()

new_model = """
class UserBadge(Base):
    __tablename__ = "user_badges"
    __table_args__ = (UniqueConstraint("user_id", "badge_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"))
    badge_key: Mapped[str] = mapped_column(String(50))
    awarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
"""

if "class UserBadge(Base):" not in content:
    with open(file_path, 'a') as f:
        f.write(new_model)
    print("Added UserBadge to models.py")
