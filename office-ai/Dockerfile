FROM python:3.11-slim

WORKDIR /srv

# System deps for pymupdf + chromadb + sentence-transformers wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY .streamlit ./.streamlit
COPY app.py bulk_import.py .env.example ./
COPY modules ./modules

ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501

EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]