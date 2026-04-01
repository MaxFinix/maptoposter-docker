FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    POSTERS_DIR=/app/data/posters \
    CACHE_DIR=/app/data/cache \
    FONTS_CACHE_DIR=/app/data/fonts-cache \
    MPLCONFIGDIR=/app/data/cache/matplotlib

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN mkdir -p /app/data/posters /app/data/cache /app/data/fonts-cache

EXPOSE 8000

CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8000"]
