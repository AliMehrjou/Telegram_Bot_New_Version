FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کل پروژه داخل پوشه matching_bot_project
RUN mkdir -p /app/matching_bot_project
COPY . /app/matching_bot_project/
RUN touch /app/matching_bot_project/__init__.py

# run.py رو هم در ریشه /app بذار
RUN cp /app/matching_bot_project/run.py /app/run.py

WORKDIR /app

CMD ["python", "run.py"]