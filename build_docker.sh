#!/usr/bin/env bash
#
# Build and push the celery metric exporter image to Docker Hub.
#
#   Image:    caveconnectome/celery-metric-exporter:<git-tag>
#   Platform: linux/amd64 (GKE nodes are amd64; cross-built from an arm64 Mac
#             via buildx)
#
# The image tag is taken from the git tag on the CURRENT commit. If HEAD has no
# tag, the build fails -- the pushed tag must always correspond to a git tag.
#
# Requires: `docker login` to Docker Hub with push access to the caveconnectome org.
#
set -euo pipefail

REGISTRY="caveconnectome"
IMAGE="celery-metric-exporter"
PLATFORM="linux/amd64"

# Always run from the repo root (this script's directory) so the build context
# and git queries are correct regardless of where it's invoked from.
cd "$(dirname "$0")"

# --- tag: derive from the git tag on HEAD, fail if there is none -------------
GIT_TAG="$(git tag --points-at HEAD | head -n1)"
if [ -z "$GIT_TAG" ]; then
  echo "ERROR: HEAD ($(git rev-parse --short HEAD)) has no git tag." >&2
  echo "       Tag the commit first, e.g.:  git tag v6 && git push origin v6" >&2
  exit 1
fi

if [ "$(git tag --points-at HEAD | grep -c .)" -gt 1 ]; then
  echo "NOTE: multiple tags on HEAD; using '${GIT_TAG}'. All: $(git tag --points-at HEAD | tr '\n' ' ')" >&2
fi

# --- refuse a dirty tree so the tag matches exactly what is built ------------
# The image is built from the working directory (COPY . in the Dockerfile), so a
# dirty tree would produce an image that does not match tag '${GIT_TAG}'.
# Set ALLOW_DIRTY=1 to override (e.g. for a local test build).
if [ -n "$(git status --porcelain)" ] && [ "${ALLOW_DIRTY:-0}" != "1" ]; then
  echo "ERROR: working tree is dirty; the image would not match tag '${GIT_TAG}'." >&2
  echo "       Commit (and push) your changes, or set ALLOW_DIRTY=1 to override." >&2
  exit 1
fi

FULL_IMAGE="${REGISTRY}/${IMAGE}:${GIT_TAG}"
echo "Building and pushing ${FULL_IMAGE} for ${PLATFORM} ..."

docker buildx build \
  --platform "${PLATFORM}" \
  --tag "${FULL_IMAGE}" \
  --push \
  .

echo "Done: pushed ${FULL_IMAGE}"
