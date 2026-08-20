FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BOT_DB=/data/bot.db \
    BOT_CONFIG=/app/config.yaml

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY config.example.yaml ./config.example.yaml

# Persist the SQLite database across restarts.
VOLUME ["/data"]

EXPOSE 5000

# Default: run the dashboard. Override the command to run the scanner loop, e.g.
#   docker run ... python -m bot.cli run
CMD ["python", "-m", "bot.cli", "web", "--host", "0.0.0.0", "--port", "5000"]
