FROM python:3.14-slim

COPY void42-ca.crt /usr/local/share/ca-certificates/void42-ca.crt
# - ca-certificates: trust the void42 internal PKI (Nexus + Vault PKI).
# - libmagic1: native lib python-magic loads at import time for MIME
#   sniffing; without it the upload pipeline can't tell a PNG from a
#   polyglot at start-up and the app fails to import.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libmagic1 && \
    update-ca-certificates && rm -rf /var/lib/apt/lists/*

ENV PIP_INDEX_URL=https://nexus.void42.internal/repository/pypi-proxy/simple/ \
    PIP_TRUSTED_HOST=nexus.void42.internal

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .

USER appuser

CMD ["python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
