FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY frontend/ ./frontend/

ENV PLUG_IP=""
ENV PLUG_TOKEN=""
ENV COLLECT_INTERVAL=60
ENV DB_PATH=/data/power_data.db
ENV PORT=8080
ENV RETENTION_DAYS=0
ENV TZ=Asia/Shanghai

EXPOSE 8080

CMD ["python3", "-m", "app"]