FROM python:3.11-slim AS base
WORKDIR /app
COPY requirements.txt .

RUN apt-get update && \
    apt-get install --no-install-recommends -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "alttexter-ghclient.py"]