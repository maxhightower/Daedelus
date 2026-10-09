#!/usr/bin/env bash
# Build the Daedelus images with the tags compose.yml expects.
#
#   deploy/build-images.sh [--base IMAGE] [--node-image IMAGE] [--proxy URL --ca FILE]
#                          [--blender-dir DIR] [--only control,office,blender,code]
#
#   --base         Python base image (default python:3.12-slim-bookworm; ubuntu:24.04 works)
#   --proxy/--ca   build behind a TLS-intercepting egress proxy: the CA is passed as a build
#                  secret (never stored in an image) and the build uses the host network
#   --blender-dir  use a local Blender build (directory containing ./blender) instead of
#                  downloading it from download.blender.org
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE=python:3.12-slim-bookworm
NODE=node:22-bookworm-slim
PROXY=""; CA=""; BLENDER_DIR=""; ONLY="control,office,blender,code"
while [ $# -gt 0 ]; do
  case "$1" in
    --base) BASE="$2"; shift 2;;
    --node-image) NODE="$2"; shift 2;;
    --proxy) PROXY="$2"; shift 2;;
    --ca) CA="$2"; shift 2;;
    --blender-dir) BLENDER_DIR="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
common=(--build-arg "PYTHON_IMAGE=$BASE" --build-arg "NODE_IMAGE=$NODE")
if [ -n "$PROXY" ]; then
  common+=(--network host --build-arg "HTTPS_PROXY=$PROXY" --build-arg "https_proxy=$PROXY"
           --build-arg "HTTP_PROXY=$PROXY" --build-arg "http_proxy=$PROXY"
           --build-arg "NO_PROXY=${NO_PROXY:-}" --build-arg "no_proxy=${NO_PROXY:-}")
fi
[ -n "$CA" ] && common+=(--secret "id=ca,src=$CA")
build() { # tag dockerfile extra-args...
  local tag="$1" df="$2"; shift 2
  echo "== building $tag"
  docker buildx build --load "${common[@]}" "$@" -f "$ROOT/deploy/$df" -t "$tag" "$ROOT"
}
IFS=, read -ra want <<< "$ONLY"
for w in "${want[@]}"; do
  case "$w" in
    control) build daedelus-control:local control.Dockerfile;;
    office) build daedelus-worker-office:local worker.Dockerfile --build-arg WITH_OFFICE=1;;
    code) build daedelus-worker-code:local worker.Dockerfile --build-arg WITH_GIT=1;;
    blender)
      extra=(--build-arg WITH_BLENDER=1)
      [ -n "$BLENDER_DIR" ] && extra+=(--build-context "blender=$BLENDER_DIR")
      build daedelus-worker-blender:local worker.Dockerfile "${extra[@]}";;
    *) echo "unknown image $w" >&2; exit 2;;
  esac
done
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep '^daedelus-' || true
