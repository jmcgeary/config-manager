import os
import asyncio
import time
from typing import Dict, Any, Optional, Set, List
from contextlib import asynccontextmanager
from datetime import datetime
import structlog
from fastapi import FastAPI, HTTPException, Depends, WebSocket
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from prometheus_client import Counter, Histogram, generate_latest
from typing import Tuple
import uvicorn

from .etcd_client import EtcdClient
from .models import (
    ConfigResponse, ConfigBatchResponse, ConfigValue, ConfigMetadata,
    EmergencyOverrideRequest, DeployRequest, VersionHistoryResponse, 
    DeployResponse
)
from .websocket import WebSocketManager

logger = structlog.get_logger()

# Metrics - Lazy initialization to avoid duplicate registration
REQUEST_COUNT = None
REQUEST_DURATION = None

def get_metrics() -> Tuple[Counter, Histogram]:
    global REQUEST_COUNT, REQUEST_DURATION
    if REQUEST_COUNT is None:
        REQUEST_COUNT = Counter('config_requests_total', 'Total config requests', ['method', 'endpoint', 'status'])
        REQUEST_DURATION = Histogram('config_request_duration_seconds', 'Request duration')
    return REQUEST_COUNT, REQUEST_DURATION


class ConfigService:
    def __init__(self):
        self.etcd_client: Optional[EtcdClient] = None
        self.websocket_manager: Optional[WebSocketManager] = None
        # Real-time cache - always fresh due to etcd watches
        self.real_time_cache: Dict[str, ConfigValue] = {}
        # Timestamp when cache was last updated per namespace:environment
        self.cache_timestamps: Dict[str, float] = {}
        # Demo chaos: set of simulated-down endpoints (host:port)
        self.sim_down_endpoints: Set[str] = set()
    
    async def initialize(self):
        etcd_endpoints = os.getenv('ETCD_ENDPOINTS', 'localhost:2379').split(',')
        self.etcd_client = EtcdClient(etcd_endpoints)
        await self.etcd_client.connect()
        
        # Initialize WebSocket manager for real-time updates
        self.websocket_manager = WebSocketManager(self.etcd_client)
        
        # Set up etcd watch to maintain real-time cache
        await self._setup_cache_updates()
        
        logger.info("Config service initialized", endpoints=etcd_endpoints)
    
    async def _setup_cache_updates(self):
        """Set up etcd watches to keep the sidecar cache always fresh"""
        await self.etcd_client.watch_prefix("/config", self._update_cache_on_change)
        logger.info("Sidecar cache watch established for real-time updates")
    
    async def _update_cache_on_change(self, key: str, config_value: Optional[ConfigValue]):
        """Update the sidecar's cache when etcd changes"""
        if config_value is not None:
            self.real_time_cache[key] = config_value
            logger.debug("Cache updated", key=key)
        else:
            # Config was deleted
            if key in self.real_time_cache:
                del self.real_time_cache[key]
                logger.debug("Cache entry deleted", key=key)
        
        # Update timestamp for this namespace:environment
        parts = key.strip('/').split('/')
        if len(parts) >= 3 and parts[0] == 'config':
            namespace_env = f"{parts[1]}:{parts[2]}"
            self.cache_timestamps[namespace_env] = time.time()
    
    def _get_from_cache(self, namespace: str, environment: str, key: Optional[str] = None) -> Optional[ConfigValue]:
        """Get from real-time cache"""
        if key:
            cache_key = f"/config/{namespace}/{environment}/{key}"
            return self.real_time_cache.get(cache_key)
        return None
    
    def _get_all_from_cache(self, namespace: str, environment: str) -> Dict[str, ConfigValue]:
        """Get all configs for namespace/environment from real-time cache"""
        prefix = f"/config/{namespace}/{environment}/"
        result = {}
        
        for cache_key, config_value in self.real_time_cache.items():
            if cache_key.startswith(prefix):
                # Extract the config key from the full etcd key
                config_key = cache_key[len(prefix):]
                result[config_key] = config_value
        
        return result
    
    async def cleanup(self):
        if self.etcd_client:
            await self.etcd_client.close()


config_service = ConfigService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await config_service.initialize()
    yield
    await config_service.cleanup()


app = FastAPI(
    title="Config Service",
    description="A production-ready, distributed configuration management service built on etcd",
    version="0.1.0",
    lifespan=lifespan
)

# Static files and SPA fallback (Milestone 3)
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    # Serve static assets under /static (app.js, styles.css, etc.)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def spa_index_root():
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/ns/{slug:path}", include_in_schema=False)
    async def spa_index_namespace(slug: str):
        # Serve SPA entry for namespace paths
        return FileResponse(_STATIC_DIR / "index.html")


def get_etcd_client() -> EtcdClient:
    if not config_service.etcd_client:
        raise HTTPException(status_code=503, detail="etcd client not initialized")
    return config_service.etcd_client


# Chaos simulation endpoints for demo controls
@app.post("/v1/chaos/kill-leader")
async def chaos_kill_leader(etcd_client: EtcdClient = Depends(get_etcd_client)):
    """Simulate killing the current (effective) leader.

    Uses simulated-down state to determine which member is currently acting as leader
    from the app's perspective, then adds that endpoint to the down set.
    """
    status = await etcd_client.get_cluster_status()
    members: List[Dict[str, Any]] = status.get('members', [])
    # Apply current simulated downs to compute effective leader
    for m in members:
        if m.get('endpoint') in config_service.sim_down_endpoints:
            m['healthy'] = False
    effective_leader = next((m for m in members if m.get('is_leader')), None)
    if effective_leader and not effective_leader.get('healthy'):
        effective_leader = None
    if not effective_leader:
        effective_leader = next((m for m in members if m.get('healthy')), None)
    if not effective_leader:
        raise HTTPException(status_code=503, detail="No healthy members to kill")
    ep = effective_leader.get('endpoint')
    if not ep:
        raise HTTPException(status_code=500, detail="Leader endpoint unknown")
    config_service.sim_down_endpoints.add(ep)
    logger.info("Chaos: leader marked down", endpoint=ep)
    return {"success": True, "down": list(config_service.sim_down_endpoints)}


@app.post("/v1/chaos/revive")
async def chaos_revive_all():
    """Clear all simulated down endpoints (bring all back up)."""
    config_service.sim_down_endpoints.clear()
    logger.info("Chaos: all endpoints revived")
    return {"success": True}


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        if config_service.etcd_client:
            # Simple connectivity test
            await config_service.etcd_client.get_config("_health", "test", "check")
        return {"status": "healthy"}
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(status_code=503, detail="Service unhealthy")


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return generate_latest()


@app.get("/cluster/status")
async def cluster_status(etcd_client: EtcdClient = Depends(get_etcd_client)):
    """Return etcd cluster member list, leader, and health summary."""
    try:
        status = await etcd_client.get_cluster_status()
        # Apply simulated chaos
        if config_service.sim_down_endpoints:
            members: List[Dict[str, Any]] = status.get('members', [])
            for m in members:
                if m.get('endpoint') in config_service.sim_down_endpoints:
                    m['healthy'] = False
            # If current leader unhealthy, promote first healthy
            current_leader = next((m for m in members if m.get('is_leader')), None)
            if current_leader and not current_leader.get('healthy'):
                for m in members:
                    if m.get('healthy'):
                        for x in members:
                            x['is_leader'] = (x is m)
                        status['leader_id'] = m.get('id')
                        break
        return status
    except Exception as e:
        logger.error("Failed to get cluster status", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get cluster status")


@app.get("/v1/config/{namespace}/{environment}")
async def get_all_config(
    namespace: str, 
    environment: str,
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Get all configuration for a namespace/environment"""
    request_count, request_duration = get_metrics()
    request_count.labels(method='GET', endpoint='all_config', status='attempt').inc()
    
    try:
        with request_duration.time():
            # Try cache first (real-time updated)
            configs = config_service._get_all_from_cache(namespace, environment)
            
            # If cache is empty, fallback to etcd and populate cache
            if not configs:
                logger.debug("Cache miss, fetching from etcd", namespace=namespace, environment=environment)
                configs = await etcd_client.get_all_configs(namespace, environment)
                
                # Populate cache for next time
                for key, config_value in configs.items():
                    cache_key = f"/config/{namespace}/{environment}/{key}"
                    config_service.real_time_cache[cache_key] = config_value
        
        request_count.labels(method='GET', endpoint='all_config', status='success').inc()
        
        return ConfigBatchResponse(
            namespace=namespace,
            environment=environment,
            configs=configs
        )
        
    except Exception as e:
        request_count.labels(method='GET', endpoint='all_config', status='error').inc()
        logger.error("Failed to get all configs", 
                   namespace=namespace, environment=environment, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve configuration")


@app.get("/v1/config/{namespace}/{environment}/{key:path}")
async def get_config(
    namespace: str, 
    environment: str, 
    key: str,
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Get a specific configuration value"""
    request_count, request_duration = get_metrics()
    request_count.labels(method='GET', endpoint='single_config', status='attempt').inc()
    
    try:
        with request_duration.time():
            # Try cache first (real-time updated)
            config_value = config_service._get_from_cache(namespace, environment, key)
            
            # If not in cache, fallback to etcd and cache the result
            if config_value is None:
                logger.debug("Cache miss, fetching from etcd", namespace=namespace, environment=environment, key=key)
                config_value = await etcd_client.get_config(namespace, environment, key)
                
                # Cache the result if found
                if config_value is not None:
                    cache_key = f"/config/{namespace}/{environment}/{key}"
                    config_service.real_time_cache[cache_key] = config_value
        
        if config_value is None:
            request_count.labels(method='GET', endpoint='single_config', status='not_found').inc()
            raise HTTPException(status_code=404, detail="Configuration not found")
        
        request_count.labels(method='GET', endpoint='single_config', status='success').inc()
        
        return ConfigResponse(
            key=key,
            value=config_value.value,
            version=config_value.version,
            metadata=config_value.metadata
        )
        
    except HTTPException:
        raise
    except Exception as e:
        request_count.labels(method='GET', endpoint='single_config', status='error').inc()
        logger.error("Failed to get config", 
                   namespace=namespace, environment=environment, key=key, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve configuration")


@app.websocket("/v1/watch/{namespace}/{environment}")
async def websocket_endpoint(websocket: WebSocket, namespace: str, environment: str):
    """WebSocket endpoint for real-time configuration updates"""
    if not config_service.websocket_manager:
        await websocket.close(code=1011, reason="WebSocket manager not initialized")
        return
    
    await config_service.websocket_manager.handle_websocket(websocket, namespace, environment)


# Management API endpoints
# Standard write endpoint (preferred)
@app.post("/v1/config/{namespace}/{environment}/{key:path}")
async def write_config(
    namespace: str,
    environment: str,
    key: str,
    request: EmergencyOverrideRequest,  # reuse schema: { value, reason, created_by }
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Create or update a configuration value (non-emergency path)."""
    request_count, request_duration = get_metrics()
    request_count.labels(method='POST', endpoint='write_config', status='attempt').inc()

    try:
        with request_duration.time():
            creator = request.created_by or "ui"
            reason = request.reason or ""
            config_value = ConfigValue(
                value=request.value,
                version=f"manual-{int(asyncio.get_event_loop().time())}",
                metadata=ConfigMetadata(
                    created_by=creator,
                    created_at=datetime.now(),
                    approved_by=None,
                    git_commit=None
                )
            )

            started_at = datetime.now()
            await etcd_client.set_config(namespace, environment, key, config_value)

            # After write, check replication across endpoints (best-effort)
            replication_log = []
            try:
                replication_log = await etcd_client.check_replication(
                    namespace, environment, key, config_value.value,
                    sim_down=list(config_service.sim_down_endpoints)
                )
            except Exception as e:
                logger.warning("Replication check failed", error=str(e))
            completed_at = datetime.now()

        request_count.labels(method='POST', endpoint='write_config', status='success').inc()
        logger.info(
            "Config write applied",
            namespace=namespace,
            environment=environment,
            key=key,
            created_by=creator,
            reason=reason,
        )
        return {
            "success": True,
            "message": "Configuration updated successfully",
            "version": config_value.version,
            "replication_log": replication_log,
            "replication_context": {
                "namespace": namespace,
                "environment": environment,
                "key": key,
                "value": config_value.value,
                "version": config_value.version,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            },
        }
    except Exception as e:
        request_count.labels(method='POST', endpoint='write_config', status='error').inc()
        logger.error(
            "Failed to write configuration",
            namespace=namespace,
            environment=environment,
            key=key,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="Failed to write configuration")


# TODO: this doesn't need to be an "emergency" endpoint; we can support a normal POST update
@app.post("/v1/emergency/{namespace}/{environment}/{key:path}")
async def emergency_override(
    namespace: str,
    environment: str,
    key: str,
    request: EmergencyOverrideRequest,
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Emergency override for immediate configuration changes"""
    request_count, request_duration = get_metrics()
    request_count.labels(method='POST', endpoint='emergency_override', status='attempt').inc()
    
    try:
        with request_duration.time():
            # Create config value with emergency metadata
            creator = request.created_by or "ui"
            reason = request.reason or ""
            config_value = ConfigValue(
                value=request.value,
                version=f"emergency-{int(asyncio.get_event_loop().time())}",
                metadata=ConfigMetadata(
                    created_by=creator,
                    created_at=datetime.now(),
                    approved_by=None,
                    git_commit=None
                )
            )
            
            started_at = datetime.now()
            await etcd_client.set_config(namespace, environment, key, config_value)
            # Replication log for emergency path as well
            replication_log = []
            try:
                replication_log = await etcd_client.check_replication(
                    namespace, environment, key, config_value.value,
                    sim_down=list(config_service.sim_down_endpoints)
                )
            except Exception as e:
                logger.warning("Replication check failed (emergency)", error=str(e))
            completed_at = datetime.now()
        
        request_count.labels(method='POST', endpoint='emergency_override', status='success').inc()
        
        logger.info("Emergency override applied", 
                   namespace=namespace, environment=environment, 
                   key=key, created_by=creator, reason=reason)
        
        return {
            "success": True,
            "message": "Emergency override applied successfully",
            "version": config_value.version,
            "replication_log": replication_log,
            "replication_context": {
                "namespace": namespace,
                "environment": environment,
                "key": key,
                "value": config_value.value,
                "version": config_value.version,
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            },
        }
        
    except Exception as e:
        request_count.labels(method='POST', endpoint='emergency_override', status='error').inc()
        logger.error("Failed to apply emergency override", 
                   namespace=namespace, environment=environment, 
                   key=key, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to apply emergency override")


# TODO: we dont need to support configs deploying this way for now. just a regular push is fine.
# actually I think we can just rename this or update he comments
@app.post("/v1/deploy/{namespace}/{environment}")
async def deploy_configs(
    namespace: str,
    environment: str,
    request: DeployRequest,
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Deploy configuration changes from Git"""
    request_count, request_duration = get_metrics()
    request_count.labels(method='POST', endpoint='deploy', status='attempt').inc()
    
    try:
        with request_duration.time():
            deployed_count = 0
            
            # Deploy each configuration
            for key, value in request.configs.items():
                config_value = ConfigValue(
                    value=value,
                    version=request.git_ref,
                    metadata=ConfigMetadata(
                        created_by=request.created_by,
                        created_at=datetime.now(),
                        approved_by=None,  # Could be set based on Git approval process
                        git_commit=request.git_ref
                    )
                )
                
                await etcd_client.set_config(namespace, environment, key, config_value)
                deployed_count += 1
        
        request_count.labels(method='POST', endpoint='deploy', status='success').inc()
        
        logger.info("Configuration deployment completed", 
                   namespace=namespace, environment=environment,
                   git_ref=request.git_ref, deployed_count=deployed_count)
        
        return DeployResponse(
            success=True,
            message=f"Successfully deployed {deployed_count} configurations",
            version=request.git_ref,
            deployed_count=deployed_count
        )
        
    except Exception as e:
        request_count.labels(method='POST', endpoint='deploy', status='error').inc()
        logger.error("Failed to deploy configurations", 
                   namespace=namespace, environment=environment, 
                   git_ref=request.git_ref, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to deploy configurations")


@app.get("/v1/versions/{namespace}/{environment}")
async def get_version_history(
    namespace: str,
    environment: str,
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Get version history for configurations"""
    request_count, request_duration = get_metrics()
    request_count.labels(method='GET', endpoint='version_history', status='attempt').inc()
    
    try:
        with request_duration.time():
            versions = await etcd_client.get_all_versions(namespace, environment)
        
        request_count.labels(method='GET', endpoint='version_history', status='success').inc()
        
        return VersionHistoryResponse(
            namespace=namespace,
            environment=environment,
            versions=versions
        )
        
    except Exception as e:
        request_count.labels(method='GET', endpoint='version_history', status='error').inc()
        logger.error("Failed to get version history", 
                   namespace=namespace, environment=environment, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve version history")


@app.delete("/v1/config/{namespace}/{environment}/{key:path}")
async def delete_config(
    namespace: str,
    environment: str,
    key: str,
    etcd_client: EtcdClient = Depends(get_etcd_client)
):
    """Delete a configuration key"""
    request_count, request_duration = get_metrics()
    request_count.labels(method='DELETE', endpoint='delete_config', status='attempt').inc()
    
    try:
        with request_duration.time():
            deleted = await etcd_client.delete_config(namespace, environment, key)
        
        if not deleted:
            request_count.labels(method='DELETE', endpoint='delete_config', status='not_found').inc()
            raise HTTPException(status_code=404, detail="Configuration not found")
        
        request_count.labels(method='DELETE', endpoint='delete_config', status='success').inc()
        
        logger.info("Configuration deleted", 
                   namespace=namespace, environment=environment, key=key)
        
        return {"success": True, "message": "Configuration deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        request_count.labels(method='DELETE', endpoint='delete_config', status='error').inc()
        logger.error("Failed to delete configuration", 
                   namespace=namespace, environment=environment, 
                   key=key, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to delete configuration")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


def main():
    """Main entry point for the config service"""
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '8080'))
    log_level = os.getenv('LOG_LEVEL', 'info')
    
    uvicorn.run(
        "config_service.server:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False
    )


if __name__ == "__main__":
    main()
