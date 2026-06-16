import re

file_path = 'database/models/models.py'

with open(file_path, 'r') as f:
    content = f.read()

new_model = """
class UserLike(Base):
    __tablename__ = "user_likes"
    __table_args__ = (UniqueConstraint("liker_id", "liked_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    liker_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"))
    liked_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.tg_id", ondelete="CASCADE"))
    is_pass: Mapped[bool] = mapped_column(Boolean, default=False)  # True = Pass, False = Like
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
"""

if "class UserLike(Base):" not in content:
    with open(file_path, 'a') as f:
        f.write(new_model)
    print("Added UserLike to models.py")
