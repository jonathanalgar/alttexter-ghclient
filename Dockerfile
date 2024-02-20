FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y git && apt clean

COPY requirements.txt /
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir /app
WORKDIR /app
ADD . /app

CMD python alttexter-ghclient.py