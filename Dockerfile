FROM python:3.11-slim

WORKDIR /srv

# Minimal system deps for pymupdf wheel + psycopg2.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY create_partner.py ./create_partner.py
COPY ensure_partner.py ./ensure_partner.py

ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "ensure_partner.py"]
