# Config Service Specification

## Overview

A production-ready, distributed configuration management service built on etcd. This service provides a simple, secure way for applications to access configuration data at runtime through a sidecar pattern, enabling zero-downtime configuration updates and seamless local development.

## Project Objectives

### Technical Objectives
- **Zero-downtime configuration updates** with real-time propagation
- **High availability** with automatic failover across etcd cluster nodes
- **Developer-friendly API** with transparent caching and connection management
- **Production-ready security** with pluggable authentication backends
- **Operational simplicity** through sidecar deployment pattern
- **Schema validation** (optional) for type safety and documentation

### Non-Technical Objectives
- **Open source** library that's easily adoptable by any organization
- **Minimal operational overhead** for development teams
- **5-minute local setup** for testing and development
- **Clear separation** between platform team (etcd cluster) and app teams (sidecars)
- **Smooth migration path** from existing configuration solutions

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Application   │───→│ Config Service   │───→│  etcd Cluster   │
│                 │    │    (Sidecar)     │    │  (Platform)     │
│ config.get(key) │    │ • Local cache    │    │ • HA storage    │
│                 │    │ • WebSocket      │    │ • Watch events  │
│                 │    │ • Auth           │    │ • Consistency   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

### Data Model
How configuration is stored internally in etcd:

```yaml
# etcd key: /config/payment-service/production/database.pool_size
{
  "value": 50,                     # The actual config value
  "version": "v1.2.3",             # Git commit/tag reference  
  "metadata": {
    "created_by": "team-payments",
    "created_at": "2025-01-15T10:30:00Z",
    "approved_by": "alice@company.com",
    "git_commit": "abc123def456"
  }
}
```

**Key Structure**: `/config/{namespace}/{environment}/{key}`
- **Namespace**: Service identifier (e.g., "payment-service")  
- **Environment**: Deployment environment (e.g., "production", "staging")
- **Key**: Configuration key with dots as literal characters (e.g., "database.pool_size")

**Note**: Dots in keys are treated as literal characters, not path separators. This keeps etcd keys flat while allowing hierarchical naming in application code.

## API Design

### Runtime API (Read-Only)
Applications use these endpoints through the client library (watch functionality is handled internally via WebSocket):

```http
GET /v1/config/{namespace}/{environment}           # Get all config
GET /v1/config/{namespace}/{environment}/{key}     # Get specific key
GET /health                                         # Health check
GET /metrics                                        # Prometheus metrics
```

### Management API
Platform teams use these for deployments:

```http
POST /v1/deploy/{namespace}/{environment}              # Deploy from Git
PUT /v1/emergency/{namespace}/{environment}/{key}      # Manual override
GET /v1/versions/{namespace}/{environment}             # Version history
POST /v1/rollback/{namespace}/{environment}/{version}  # Rollback to version
DELETE /v1/config/{namespace}/{environment}/{key}      # Delete key
```

### Client Library Interface
```python
from config_service import ConfigClient

# Initialize (auto-connects to sidecar)
config = ConfigClient()

# Simple read operations
pool_size = config.get("database.pool_size")
debug_mode = config.get("debug", default=False)
all_config = config.get_all()

# Type-safe reads (if schema present)
pool_size = config.get_int("database.pool_size", min_val=1, max_val=1000)
```

## Security & Authentication

### Service Identity & Namespace Isolation
Each service receives unique credentials that restrict access to only its own configuration namespace:

```yaml
# Service token (JWT or similar) - limits access to service's own config
{
  "service": "payment-service",
  "environment": "production", 
  "team": "payments",
  "permissions": ["read:payment-service/*"]  # Can only read payment-service configs
}

# Example: payment-service CANNOT read user-service configs
# ✅ Allowed: GET /v1/config/payment-service/production/database.host
# ❌ Denied:  GET /v1/config/user-service/production/database.host
```

### Pluggable Auth Backends
```yaml
# config-service.yaml
auth:
  backend: "kubernetes"  # kubernetes, vault, static, aws-iam
  
  kubernetes:
    service_account: "payment-service"
    
  vault:
    role: "payment-service"
    auth_path: "auth/kubernetes"
    
  static:
    tokens_file: "/etc/tokens.yaml"
```

### RBAC Rules
- Services can only read their own namespace: `{service-name}/*`
- Admin operations require separate elevated permissions
- All operations are logged with service identity

## Deployment Patterns

### Local Development
```yaml
# docker-compose.yml - Everything in one file
services:
  my-app:
    build: .
    depends_on: [config-service]
    
  config-service:
    image: ghcr.io/yourorg/config-service:latest
    environment:
      - ETCD_ENDPOINTS=etcd:2379
      - SERVICE_NAME=my-app
      - CONFIG_AUTH=none
    depends_on: [etcd]
    
  etcd:
    image: quay.io/coreos/etcd:v3.5.3
    environment:
      - ETCD_LISTEN_CLIENT_URLS=http://0.0.0.0:2379
      - ETCD_ADVERTISE_CLIENT_URLS=http://etcd:2379
```

### Production Deployment
```yaml
# docker-compose.yml - References external etcd
services:
  my-app:
    image: my-app:v1.2.3
    depends_on: [config-service]
    
  config-service:
    image: ghcr.io/yourorg/config-service:latest
    environment:
      - ETCD_ENDPOINTS=${ETCD_ENDPOINTS}  # Platform-provided
      - SERVICE_NAME=${SERVICE_NAME}
      - CONFIG_TOKEN_FILE=/etc/secrets/token
    volumes:
      - /etc/secrets:/etc/secrets:ro
```

## Configuration Management Workflows

### GitOps (Primary)
```
1. Developer edits config/production.yaml
2. Opens pull request with changes  
3. CI validates against schema (if present)
4. Code review and approval
5. Merge triggers deployment to staging
6. Manual promotion to production via deployment API
```

### Emergency Override (Secondary)
```
1. On-call engineer uses admin API for immediate change
2. System auto-creates follow-up PR for Git persistence
3. Manual review required to make permanent
```

## Schema System (Optional)

### Schema Definition
```yaml
# payment-service/schema.yaml
database:
  pool_size:
    type: integer
    min: 1
    max: 1000
    default: 10
    description: "Database connection pool size"
    
  host:
    type: string
    pattern: "^[a-zA-Z0-9.-]+$"
    description: "Database hostname"

feature_flags:
  new_checkout:
    type: boolean
    default: false
    description: "Enable new checkout flow"
    rollout_strategy: "canary"
```

### Schema Benefits
- **Validation**: Type checking and constraints in CI/CD
- **Documentation**: Auto-generated docs from schema
- **Defaults**: Return schema defaults for missing keys
- **IDE Support**: Autocomplete and validation in editors
- **Migration**: Guided schema evolution and deprecation

### Default Value Resolution Precedence
When a configuration key is requested, values are resolved in this order:
1. **etcd stored value** - If key exists in etcd, use that value
2. **Schema default** - If schema exists and defines a default, use schema default
3. **Client default** - Use default provided in `config.get(key, default=value)` call
4. **Error** - If none of the above exist, raise KeyError/return null

## Configuration Validation & Safety

### Value Validation
- **Size limits**: Configuration values limited to 1MB, keys to 1KB
- **JSON validation**: All values must be valid JSON (strings, numbers, booleans, objects, arrays)
- **Encoding**: UTF-8 encoding required for all text values
- **Reserved keys**: Prevent use of system reserved prefixes (e.g., "_system", "_meta")

### Error Handling
- **Malformed data**: Invalid JSON values are rejected with descriptive errors
- **Connection failures**: Client falls back to cache when etcd unavailable
- **Partial failures**: Service continues operating with degraded functionality

### Safety Mechanisms
- **Gradual rollout**: Configuration changes can be deployed incrementally
- **Circuit breaker**: Automatic fallback when error rates exceed threshold
- **Backup/restore**: Regular automated backups of configuration state

## Outstanding Design Decisions (TODOs)

### Concurrent Update Resolution
- **Problem**: How to handle conflicting updates (GitOps vs emergency overrides vs simultaneous changes)
- **Options**: Last-write-wins, conflict detection with manual resolution, or locks
- **Decision needed**: Before Milestone 2 implementation

### Authentication System Enhancements
- **Token rotation**: Automated token lifecycle management
- **Multi-factor auth**: Integration with enterprise identity providers
- **Audit requirements**: Compliance with SOC2/GDPR audit trails
- **Decision needed**: Before Milestone 3 implementation

### Failover Behavior Details
- **etcd partial outage**: Client behavior when some etcd nodes are down
- **Cache expiration**: How long to serve stale data during outages
- **Network partitions**: Behavior during split-brain scenarios
- **Decision needed**: Before Milestone 1 implementation

## Implementation Milestones

### Milestone 1: Core Service (2-3 weeks)
**Goal**: Basic config service with etcd backend

**Tasks**:
1. **Config Service API**
   - HTTP server with runtime endpoints (`GET /v1/config/*`)
   - etcd client with connection pooling and failover
   - JSON/YAML configuration parsing
   - Health check and metrics endpoints

2. **Client Library (Python)**
   - `ConfigClient` class with simple `get()` method
   - Automatic connection to localhost sidecar
   - Local caching with TTL
   - Error handling and graceful degradation

3. **Docker Image**
   - Multi-stage Dockerfile for config service
   - Environment variable configuration
   - Signal handling for graceful shutdown

4. **Local Development Setup**
   - Sample docker-compose.yml with etcd + config service
   - Documentation for 5-minute local setup
   - Example application integration

**Acceptance Criteria**:
- Developer can add sidecar to docker-compose and read config
- Service survives etcd node failures with automatic failover
- Client library caches responses and handles service unavailability

### Milestone 2: Real-time Updates (1-2 weeks)  
**Goal**: Zero-downtime configuration updates

**Tasks**:
1. **WebSocket/SSE Support**
   - Real-time config change notifications to clients
   - etcd watch integration for change detection
   - Connection management and reconnection logic

2. **Enhanced Client Library**
   - Automatic cache invalidation on changes
   - Background refresh mechanism
   - Connection health monitoring

3. **Management API**
   - Deployment endpoints (`POST /v1/deploy/*`)
   - Manual override endpoints (`PUT /v1/emergency/*`)
   - Version history tracking

**Acceptance Criteria**:
- Config changes propagate to apps within 1-2 seconds
- Client library handles reconnections transparently
- Apps never block on config service availability

### Milestone 3: Authentication & Security (2 weeks)
**Goal**: Production-ready security model

**Tasks**:
1. **Service Identity System**
   - JWT token-based authentication
   - Namespace-based authorization (services read only their config)
   - Token validation and RBAC enforcement

2. **Pluggable Auth Backends**
   - Static file backend for simple deployments
   - Kubernetes ServiceAccount integration
   - Plugin interface for future backends (Vault, AWS IAM)

3. **Audit Logging**
   - Structured logging for all config operations
   - Request tracing with service identity
   - Integration with observability stack

**Acceptance Criteria**:
- Services can only access their own namespace
- All config reads/writes are attributed to service identity
- Easy integration with existing identity providers

### Milestone 4: GitOps Integration (2-3 weeks)
**Goal**: Code-reviewed configuration deployments

**Tasks**:
1. **Git Repository Integration**
   - Config repository structure (`{service}/{environment}.yaml`)
   - Git webhook handlers for automatic deployments
   - Commit-based versioning and rollback

2. **CI/CD Pipeline Tools**
   - Schema validation in CI (if schema present)
   - Deployment CLI tool for CD systems
   - Integration examples (GitHub Actions, GitLab CI)

3. **Deployment Safety**
   - Staging environment validation
   - Rollback capabilities with version history
   - Emergency override workflow with auto-PR creation

**Acceptance Criteria**:
- Config changes go through code review process
- Failed deployments can be quickly rolled back
- Emergency overrides are tracked and require follow-up

### Milestone 5: Schema System (1-2 weeks)
**Goal**: Optional type safety and documentation

**Tasks**:
1. **Schema Definition Format**
   - YAML schema format with types, constraints, defaults
   - Schema validation during config updates
   - Backward compatibility checking

2. **Tooling Integration**
   - CLI tools for schema validation and docs generation
   - IDE extensions for autocomplete and validation
   - Migration assistance for schema evolution

3. **Enhanced Client Library**
   - Type-safe getters (`get_int()`, `get_bool()`)
   - Schema-aware defaults
   - Runtime validation (optional)

**Acceptance Criteria**:
- Teams can define schemas for better developer experience
- Schema violations are caught in CI before deployment
- Documentation auto-generates from schema definitions

### Milestone 6: Production Hardening (2-3 weeks)
**Goal**: Enterprise-ready reliability and observability

**Tasks**:
1. **Advanced Client Libraries**
   - Go, Java, Node.js client implementations
   - Auto-generated clients from OpenAPI spec
   - Language-specific best practices

2. **Observability & Monitoring**
   - Prometheus metrics (request rates, cache hits, errors)
   - Distributed tracing integration
   - Performance monitoring and alerting

3. **Operational Tools**
   - Administrative CLI for ops teams
   - Kubernetes operator for automated deployments
   - Migration tools from other config systems

4. **Documentation & Examples**
   - Complete API documentation
   - Integration guides for common frameworks
   - Best practices and troubleshooting guides

**Acceptance Criteria**:
- Service operates reliably under production load
- Rich monitoring and alerting for operational teams
- Clear migration path from existing solutions

### Future Enhancements (Lower Priority)
- **Gradual Rollouts**: Canary deployments with automatic rollback
- **Advanced Caching**: Multi-tier caching with Redis support  
- **Config Templates**: Environment-specific templating system
- **Compliance**: Audit trails for regulatory requirements
- **High Availability**: Multi-region etcd cluster support

## Success Metrics

### Developer Experience
- **Setup Time**: <5 minutes from zero to working local environment
- **Integration Effort**: <10 lines of code to add to existing application
- **Documentation Quality**: Developers can integrate without support

### Operational Excellence
- **Availability**: 99.9% uptime for config service
- **Latency**: <100ms p99 for config reads from cache
- **Change Velocity**: Config changes deployed within 5 minutes
- **Security**: Zero unauthorized access to configuration data

### Adoption
- **Open Source Adoption**: Active community contributions and usage
- **Enterprise Readiness**: Production deployments at multiple organizations
- **Ecosystem Integration**: Plugins for major platforms (K8s, Docker, etc.)

## Getting Started

### For Application Developers
```bash
# 1. Add sidecar to your docker-compose.yml
curl -s https://config-service.io/docker-compose.yml >> docker-compose.yml

# 2. Set your service name  
echo "SERVICE_NAME=my-app" > .env

# 3. Install client library
pip install config-service-client

# 4. Use in your application
python -c "
from config_service import ConfigClient
config = ConfigClient()
print(config.get('debug', default=False))
"
```

### For Platform Teams
```bash
# 1. Deploy etcd cluster (managed or self-hosted)
# 2. Set up service identity system
# 3. Provide teams with:
#    - ETCD_ENDPOINTS configuration
#    - Service credentials/tokens
#    - Docker image registry access
```

This specification provides a complete roadmap for building a production-ready, open source configuration service that balances simplicity for developers with the operational requirements of modern distributed systems.