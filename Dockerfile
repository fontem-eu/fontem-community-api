# ── build: venv + libmagic (native) + void42 CA ───────────────────────────────
FROM cgr.void42.internal/chainguard/python:latest-dev AS build
USER root
ENV PIP_INDEX_URL=https://nexus.void42.internal/repository/pypi-proxy/simple/ \
    PIP_TRUSTED_HOST=nexus.void42.internal
RUN apk add --no-cache libmagic
COPY void42-ca.crt /tmp/void42-ca.crt
RUN cat /tmp/void42-ca.crt >> /etc/ssl/certs/ca-certificates.crt
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── runtime: distroless + libmagic runtime lib + magic db + CA bundle ─────────
FROM cgr.void42.internal/chainguard/python:latest
WORKDIR /app
COPY --from=build /venv /venv
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /usr/lib/libmagic.so.1 /usr/lib/libmagic.so.1
COPY --from=build /usr/lib/libmagic.so.1.0.0 /usr/lib/libmagic.so.1.0.0
COPY --from=build /usr/share/misc/magic.mgc /usr/share/misc/magic.mgc
ENV PATH="/venv/bin:$PATH" \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini .
USER 65532
EXPOSE 8001
ENTRYPOINT ["/venv/bin/python", "-m", "uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8001"]
