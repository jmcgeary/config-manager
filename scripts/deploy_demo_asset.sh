#!/usr/bin/env bash
set -euo pipefail

echo "[deploy] Ensuring Docker is available..."
docker info >/dev/null 2>&1 || { echo "Docker is not available. Please start Docker Desktop or your Docker daemon."; exit 1; }

echo "[deploy] Using Python env: ${VIRTUAL_ENV:-system}" 
if command -v uv >/dev/null 2>&1; then
  echo "[deploy] Installing CDK Python deps with uv..."
  uv pip install -r infra/cdk/requirements.txt
else
  echo "[deploy] Installing CDK Python deps with pip..."
  pip install -r infra/cdk/requirements.txt
fi

echo "[deploy] Bootstrapping CDK (if needed)..."
cd infra/cdk
npx -y aws-cdk@2 bootstrap

echo "[deploy] Deploying stack with asset image..."
npx -y aws-cdk@2 deploy --require-approval never
