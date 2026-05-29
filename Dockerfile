FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk-extra \
    gcc \
    python3-dev \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY frontend/ ./frontend/

ENV PLUG_IP=""
ENV COLLECT_INTERVAL=30
ENV DB_PATH=/data/power_data.db
ENV RETENTION_DAYS=0
ENV PORT=8080
ENV TZ=Asia/Shanghai

EXPOSE 8080

CMD ["python3", "-m", "app"]