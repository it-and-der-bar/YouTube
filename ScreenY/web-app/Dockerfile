FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libjpeg62-turbo-dev \
    ffmpeg \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web-app/ /app/

RUN pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/configs /app/media /app/playlists /app/logs /app/static

EXPOSE 8000

CMD ["python", "app.py"]
