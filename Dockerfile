# قفل کردن روی نسخه پایدار Bookworm برای جلوگیری از باگ‌های مخازن ایران
FROM python:3.11-slim-bookworm

WORKDIR /app

# تزریق تنظیمات سخت‌گیرانه شبکه: 10 بار تلاش مجدد و افزایش تایم‌اوت به 300 ثانیه
RUN echo 'Acquire::Retries "10";\nAcquire::http::Timeout "300";\nAcquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99-custom-network

# تغییر مخازن اصلی به سرور ایران و بازگرداندن مخزن امنیتی به سرور اصلی
RUN sed -i 's/deb.debian.org/mirror.arvancloud.ir/g' /etc/apt/sources.list.d/debian.sources || true && \
    sed -i 's/mirror.arvancloud.ir\/debian-security/deb.debian.org\/debian-security/g' /etc/apt/sources.list.d/debian.sources || true

# نصب پکیج‌ها با فلگ fix-missing
# نصب پکیج‌ها با کمترین مصرف مموری
RUN apt-get update && apt-get install -y --fix-missing --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=1000 -i https://mirror-pypi.runflare.com/simple -r requirements.txt

RUN mkdir -p /app/matching_bot_project
COPY . /app/matching_bot_project/
RUN touch /app/matching_bot_project/__init__.py

RUN cp /app/matching_bot_project/run.py /app/run.py
# FIX L-08: previously this line was duplicated (lines 22 and 24).
RUN cp -r /app/matching_bot_project/json_files /app/json_files

WORKDIR /app

# FIX M-23: add HEALTHCHECK so orchestration can detect silent crashes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# FIX M-23: run as a non-root user for defence-in-depth.
RUN useradd --create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "run.py"]