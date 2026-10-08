# syntax=docker/dockerfile:1
# Daedelus execution worker. Tool sets are selected at build time:
#   --build-arg WITH_BLENDER=1   Blender (blender adapter)
#   --build-arg WITH_OFFICE=1    LibreOffice + poppler (spreadsheet/document/presentation)
#   --build-arg WITH_GIT=1       git (code adapter)
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}
ARG WITH_BLENDER=0
ARG WITH_OFFICE=0
ARG WITH_GIT=0
ARG BLENDER_VERSION=4.5.14
ARG BLENDER_SERIES=4.5
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export CURL_CA_BUNDLE=/run/secrets/ca; fi; \
    useradd --create-home --uid 10001 daedelus \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends ca-certificates curl xz-utils \
 && if [ "$WITH_GIT" = "1" ]; then apt-get install -y -qq --no-install-recommends git; fi \
 && if [ "$WITH_OFFICE" = "1" ]; then apt-get install -y -qq --no-install-recommends \
      libreoffice-calc libreoffice-writer libreoffice-impress poppler-utils fonts-dejavu-core; fi \
 && if [ "$WITH_BLENDER" = "1" ]; then apt-get install -y -qq --no-install-recommends \
      libxi6 libxkbcommon0 libxrender1 libxxf86vm1 libxfixes3 libsm6 libgl1 libegl1 \
    && mkdir -p /opt/blender \
    && curl -sSL https://download.blender.org/release/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz \
       | tar xJ -C /opt/blender --strip-components=1; fi \
 && rm -rf /var/lib/apt/lists/*
COPY backend/ /src/backend/
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir /src/backend \
 && if [ "$WITH_GIT" = "1" ]; then pip install --no-cache-dir "pytest>=8"; fi \
 && rm -rf /src/backend
USER daedelus
ENV DAEDELUS_BLENDER=/opt/blender/blender \
    DAEDELUS_WORKER_DIR=/tmp/daedelus-worker \
    DAEDELUS_CONTROL_URL=http://control:8765
# adapters default to every adapter whose tools are present in this image
CMD ["daedelus", "worker"]
