FROM python:3.11-slim

WORKDIR /app


RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


RUN mkdir -p /app/matching_bot_project
COPY . /app/matching_bot_project/
RUN touch /app/matching_bot_project/__init__.py


RUN cp /app/matching_bot_project/run.py /app/run.py
RUN cp -r /app/matching_bot_project/json_files /app/json_files

RUN cp -r /app/matching_bot_project/json_files /app/json_files

WORKDIR /app

CMD ["python", "run.py"]