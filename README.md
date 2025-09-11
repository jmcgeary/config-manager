# Config Manager (etcd)

This project is a dynamic config management system built on etcd with real-time updates.

The client is easy to use, and there's an actual implementation of a service using it here.

Check out https://d3cyva4awzcrtq.cloudfront.net to play around with a deployed version. (Demo to come soon)

## Overview

- Purpose: simple, reliable runtime configuration for services via a local sidecar and a small client library.
- Storage: etcd cluster with automatic failover and watches for push‑style updates.
- Access: HTTP API (FastAPI), WebSocket for realtime, and a Python client.


## Components

- Backend service (`config_service/`)
  - `server.py`: FastAPI app exposing runtime and management APIs, static UI hosting, health/metrics, and demo chaos controls.
  - `etcd_client.py`: etcd3 wrapper with endpoint failover, prefix watches, replication probing, and cluster status.
  - `websocket.py`: WebSocket manager broadcasting config changes to subscribed clients.
  - `models.py`: Pydantic models for config payloads and responses.
  - Static UI (`static/`): `index.html`, `app.js`, `styles.css` — SPA for a service running it.
- Client library (`config_client/`)
  - `ConfigClient`: simple `get`, `get_all`, `get_int`, `get_bool` with local TTL cache; defaults to `http://localhost:8080` (sidecar).
- Local demo (`docker-compose.yml`)
  - 3‑node etcd cluster and the config service; optional example app.
- Examples (`examples/`)
  - `example_usage.py`: basic integration using the Python client.
- Infra as code (`infra/cdk/`)
  - CDK stack to run the demo on AWS (Fargate + ALB + optional CloudFront).
- Scripts (`scripts/`)
  - ECR build/push and a one‑liner deploy for the demo stack.

## API (high level)

- Runtime reads
  - `GET /v1/config/{namespace}/{environment}` — all keys
  - `GET /v1/config/{namespace}/{environment}/{key}` — single key
  - `GET /v1/watch/{namespace}/{environment}` — WebSocket stream of changes
- Management (demo‑friendly)
  - `POST /v1/config/{namespace}/{environment}/{key}` — write a value
  - `POST /v1/emergency/{namespace}/{environment}/{key}` — immediate override
  - `POST /v1/deploy/{namespace}/{environment}` — batch deploy from a ref
  - `GET /v1/versions/{namespace}/{environment}` — version listing (stub)
  - `DELETE /v1/config/{namespace}/{environment}/{key}` — delete key
- Ops
  - `GET /health` — readiness probe
  - `GET /metrics` — Prometheus metrics
  - `GET /cluster/status` — etcd members, leader, health (for demo viz)
  - `POST /v1/chaos/kill-leader`, `POST /v1/chaos/revive` — demo chaos controls

## Running Locally

Prereqs: Docker and Docker Compose.

- Start the demo stack (etcd ×3 + service):
  - `docker compose up --build config-service`
- Verify:
  - `curl http://localhost:8080/health`
  - `curl http://localhost:8080/cluster/status`
- Open the demo UI:
  - `http://localhost:8080/` — generates a namespace slug and shows the live view.

Quick API smoke test:

- Write: `curl -X POST -H 'Content-Type: application/json' \
  -d '{"value": true}' http://localhost:8080/v1/config/<slug>/demo/payments/featureA`
- Read all: `curl http://localhost:8080/v1/config/<slug>/demo`
- WebSocket: `ws://localhost:8080/v1/watch/<slug>/demo` (UI connects automatically)

## Using the Python Client

- `CONFIG_SERVICE_URL` (default `http://localhost:8080`)
- `SERVICE_NAME` (namespace)
- `ENVIRONMENT` (e.g., `development`, `demo`)

Example:

```python
from config_client import ConfigClient

config = ConfigClient()  # auto: localhost:8080, SERVICE_NAME, ENVIRONMENT
enabled = config.get_bool("feature_flags.new_ui", default=False)
all_cfg = config.get_all()
```

See `examples/example_usage.py` for a runnable script.

## Configuration

- Service env vars
  - `ETCD_ENDPOINTS` — comma‑separated endpoints (e.g., `etcd1:2379,etcd2:2379,etcd3:2379`)
  - `HOST`, `PORT`, `LOG_LEVEL`
- Client env vars
  - `CONFIG_SERVICE_URL`, `SERVICE_NAME`, `ENVIRONMENT`

## Deploying the Demo to AWS

Infrastructure is provided via AWS CDK (Fargate + ALB, optional CloudFront):

- Fast path: `./scripts/deploy_demo_asset.sh` (builds the image as a CDK asset and deploys)
- Or push to ECR and deploy with context:
  - `./scripts/build_and_push_ecr.sh <account> <region> <repo> <tag>`
  - `cd infra/cdk && cdk deploy -c imageMode=ecr -c ecrRepoName=<repo> -c ecrTag=<tag>`

Outputs include an ALB DNS (and a CloudFront URL) to open the demo UI.
