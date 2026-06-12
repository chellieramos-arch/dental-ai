# ── DentAI — Azure App Service Dockerfile ─────────────────────────────────────
# Target: Azure App Service B2 (East US 2)
# Runtime: Python 3.11-slim
# Port: 8501 (Streamlit default; set WEBSITES_PORT=8501 in App Service config)

FROM python:3.11-slim

# System deps: build tools for packages with C extensions (lxml, PyMuPDF, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
        libgl1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (separate layer — cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source (chroma_db, documents, venv, .env excluded via .dockerignore)
COPY . .

# Streamlit config: disable telemetry, set server options for containerized deploy
RUN mkdir -p /app/.streamlit
RUN printf '[server]\nport = 8501\naddress = "0.0.0.0"\nheadless = true\nenableCORS = false\nenableXsrfProtection = false\n\n[browser]\ngatherUsageStats = false\n' \
    > /app/.streamlit/config.toml

EXPOSE 8501

# MODE=cloud activates Pinecone + Supabase (env vars injected by App Service config)
ENV MODE=cloud \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
