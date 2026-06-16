import re

file_path = 'database/models/models.py'

with open(file_path, 'r') as f:
    content = f.read()

new_model = """
class UserReport(Base):
    __tablename__ = "user_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"))
    reported_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(50))   # e.g. "spam", "inappropriate", "harassment"
    match_history_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("match_histories.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
"""

if "class UserReport(Base):" not in content:
    with open(file_path, 'a') as f:
        f.write(new_model)
    print("Added UserReport to models.py")

# Update User model with trust_score and report_count and invisible_mode
if "report_count: Mapped[int]" not in content:
    content = content.replace("is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)", "is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)\n    report_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)\n    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)\n    invisible_mode: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)")
    with open(file_path, 'w') as f:
        f.write(content)
    print("Added report_count, trust_score, and invisible_mode to User model")
