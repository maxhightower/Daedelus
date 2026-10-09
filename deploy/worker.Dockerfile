# syntax=docker/dockerfile:1
# Daedelus execution worker. Tool sets are selected at build time:
#   --build-arg WITH_BLENDER=1   Blender (blender adapter)
#   --build-arg WITH_OFFICE=1    LibreOffice + poppler (spreadsheet/document/presentation)
#   --build-arg WITH_GIT=1       git (code adapter)
#   --build-arg WITH_SANDBOX=1   bubblewrap: per-job namespaces (default on, V2.1)
# Base image: PYTHON_IMAGE (default python:3.12-slim-bookworm). An Ubuntu image (e.g.
# ubuntu:24.04) also works; Python then comes from the distribution.
# Blender: downloaded from download.blender.org, or taken from a local build directory with
#   --build-context blender=/path/to/blender-4.5-linux-x64   (must contain ./blender)
# Licensing: an image built WITH_BLENDER bundles GPL-licensed Blender; see
# docs/LICENSING_BRIEF.md before publishing it.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM scratch AS blender
FROM ${PYTHON_IMAGE}
ARG WITH_BLENDER=0
ARG WITH_OFFICE=0
ARG WITH_GIT=0
ARG WITH_SANDBOX=1
ARG BLENDER_VERSION=4.5.14
ARG BLENDER_SERIES=4.5
COPY deploy/docker/system-setup.sh /usr/local/sbin/dd-system-setup
COPY --from=blender / /opt/blender-local/
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export CURL_CA_BUNDLE=/run/secrets/ca; fi; \
    pk="ca-certificates curl xz-utils"; \
    command -v python3 > /dev/null || pk="$pk python3 python3-venv"; \
    python3 -c 'import ensurepip' 2>/dev/null || pk="$pk python3-venv"; \
    if [ "$WITH_GIT" = "1" ]; then pk="$pk git"; fi; \
    if [ "$WITH_SANDBOX" = "1" ]; then pk="$pk bubblewrap"; fi; \
    if [ "$WITH_OFFICE" = "1" ]; then pk="$pk libreoffice-calc libreoffice-writer libreoffice-impress poppler-utils fonts-dejavu-core"; fi; \
    if [ "$WITH_BLENDER" = "1" ]; then pk="$pk libxi6 libxkbcommon0 libxrender1 libxxf86vm1 libxfixes3 libsm6 libgl1 libegl1"; fi; \
    dd-system-setup $pk \
 && (id -u daedelus > /dev/null 2>&1 || useradd --create-home --uid 10001 daedelus) \
 && if [ "$WITH_BLENDER" = "1" ]; then \
      if [ -x /opt/blender-local/blender ]; then mv /opt/blender-local /opt/blender; \
      else mkdir -p /opt/blender && curl -sSL https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz \
           | tar xJ -C /opt/blender --strip-components=1; fi; \
    fi \
 && rm -rf /opt/blender-local \
 && python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY backend/ /src/backend/
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir /src/backend \
 && if [ "$WITH_GIT" = "1" ]; then pip install --no-cache-dir "pytest>=8"; fi \
 && rm -rf /src/backend
USER daedelus
ENV DAEDELUS_BLENDER=/opt/blender/blender \
    DAEDELUS_WORKER_DIR=/tmp/daedelus-worker \
    DAEDELUS_CONTROL_URL=http://control:8765 \
    DAEDELUS_WORKER_DEPLOYMENT=container
# adapters default to every adapter whose tools are present in this image
CMD ["daedelus", "worker"]
