import os
import asyncio
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest
from typing import Tuple
import uvicorn

from .etcd_client import EtcdClient
from .models import ConfigResponse, ConfigBatchResponse

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
        self.cache: Dict[str, Any] = {}
        self.cache_ttl = int(os.getenv('CACHE_TTL', '300'))  # 5 minutes default
    
    async def initialize(self):
        etcd_endpoints = os.getenv('ETCD_ENDPOINTS', 'localhost:2379').split(',')
        self.etcd_client = EtcdClient(etcd_endpoints)
        await self.etcd_client.connect()
        logger.info("Config service initialized", endpoints=etcd_endpoints)
    
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


def get_etcd_client() -> EtcdClient:
    if not config_service.etcd_client:
        raise HTTPException(status_code=503, detail="etcd client not initialized")
    return config_service.etcd_client


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
            configs = await etcd_client.get_all_configs(namespace, environment)
        
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


@app.get("/v1/config/{namespace}/{environment}/{key}")
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
            config_value = await etcd_client.get_config(namespace, environment, key)
        
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