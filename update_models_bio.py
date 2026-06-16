import re

file_path = 'database/models/models.py'

with open(file_path, 'r') as f:
    content = f.read()

# Check if bio is in models
if "bio: Mapped[Optional[str]]" not in content:
    # insert after tags
    content = content.replace("tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)", "tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)\n    bio: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)\n    interests: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)")
    with open(file_path, 'w') as f:
        f.write(content)
    print("Added bio and interests to models.py")
