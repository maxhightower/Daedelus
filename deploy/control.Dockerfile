# syntax=docker/dockerfile:1
# Daedelus control plane: API, studio, workflow engine, durable job queue, blob store.
# Deliberately WITHOUT Blender and LibreOffice: adapter work for remote targets runs on workers.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE} AS studio
WORKDIR /src/studio
COPY studio/package.json studio/package-lock.json ./
# optional build secret 'ca': extra CA bundle for TLS-intercepting proxies (never stored in the image)
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; npm ci --no-audit --no-fund
COPY studio/ ./
RUN npm run build

FROM ${PYTHON_IMAGE}
COPY deploy/docker/system-setup.sh /usr/local/sbin/dd-system-setup
RUN --mount=type=secret,id=ca,required=false \
    pk="ca-certificates git curl"; \
    command -v python3 > /dev/null || pk="$pk python3 python3-venv"; \
    python3 -c 'import ensurepip' 2>/dev/null || pk="$pk python3-venv"; \
    dd-system-setup $pk \
 && (id -u daedelus > /dev/null 2>&1 || useradd --create-home --uid 10001 daedelus) \
 && python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY backend/ /src/backend/
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir "/src/backend[anthropic,gemini]" && rm -rf /src/backend
COPY --from=studio /src/studio/dist /opt/daedelus/studio
RUN mkdir -p /data && chown daedelus:daedelus /data
USER daedelus
ENV DAEDELUS_WORKSPACE=/data/workspace \
    DAEDELUS_STUDIO_DIST=/opt/daedelus/studio
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
  CMD curl -fsS http://127.0.0.1:8765/api/health > /dev/null || exit 1
# a non-loopback bind is refused unless DAEDELUS_API_TOKENS is set (see SECURITY_MODEL.md)
CMD ["daedelus", "serve", "--host", "0.0.0.0", "--port", "8765"]
