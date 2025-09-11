#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 <aws-account-id> <region> <repo-name> <tag>"
  exit 1
fi

ACCOUNT_ID="$1"
REGION="$2"
REPO="$3"
TAG="$4"

IMAGE_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG"

echo "Ensuring ECR repository: $REPO"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$REPO" --image-scanning-configuration scanOnPush=true --region "$REGION" >/dev/null

echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

echo "Building image as $IMAGE_URI"
docker build -t "$IMAGE_URI" -f Dockerfile .

echo "Pushing $IMAGE_URI"
docker push "$IMAGE_URI"

echo "Done. Pushed: $IMAGE_URI"

