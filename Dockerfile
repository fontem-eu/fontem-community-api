# ── build: venv + libmagic (native) + void42 CA ───────────────────────────────
FROM cgr.void42.internal/chainguard/python:latest-dev@sha256:5eef76bbb8d9f815317da126075705202b8ca5c2a151d723e7ecdf0373d9d861 AS build
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
# The runtime needs the packages in the venv, not the tool that installed
# them: pip in a runtime image fetches and installs code (docker-build-sign
# checks runtime images for it).
RUN pip uninstall -y pip

# ── runtime: distroless + libmagic runtime lib + magic db + CA bundle ─────────
FROM cgr.void42.internal/chainguard/python:latest@sha256:a1775c7276078865461ee5714954284f12809f333433d856d720b249c65c11b2
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
