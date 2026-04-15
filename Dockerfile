FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py sheets.py srs.py claude_api.py ./

# SRS state persists via Fly.io volume mounted at /data
ENV DATA_DIR=/data

CMD ["python3", "-u", "bot.py"]
