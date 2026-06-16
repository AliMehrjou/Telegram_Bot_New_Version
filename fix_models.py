with open("database/models/models.py", "r") as f:
    c = f.read()
c = c.replace("vip_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True) = mapped_column(Integer, default=0, nullable=False)", "vip_quota: Mapped[int] = mapped_column(Integer, default=0, nullable=False)\n    vip_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)")
with open("database/models/models.py", "w") as f:
    f.write(c)
