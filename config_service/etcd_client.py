import json
import asyncio
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
import etcd3
import structlog
from .models import ConfigValue, ConfigMetadata

logger = structlog.get_logger()


class EtcdClient:
    def __init__(self, endpoints: List[str]):
        self.endpoints = endpoints
        self.client: Optional[etcd3.Etcd3Client] = None
        self._current_endpoint_index = 0
    
    async def connect(self) -> None:
        """Connect to etcd with failover support"""
        for i in range(len(self.endpoints)):
            endpoint = self.endpoints[self._current_endpoint_index]
            try:
                parsed = urlparse(f"http://{endpoint}" if "://" not in endpoint else endpoint)
                host = parsed.hostname or "localhost"
                port = parsed.port or 2379
                
                self.client = etcd3.client(host=host, port=port)
                # Test connection
                await asyncio.get_event_loop().run_in_executor(
                    None, self.client.status
                )
                logger.info("Connected to etcd", endpoint=endpoint)
                return
                
            except Exception as e:
                logger.warning("Failed to connect to etcd endpoint", 
                             endpoint=endpoint, error=str(e))
                self._current_endpoint_index = (self._current_endpoint_index + 1) % len(self.endpoints)
        
        raise ConnectionError("Failed to connect to any etcd endpoint")
    
    def _make_key(self, namespace: str, environment: str, key: Optional[str] = None) -> str:
        """Create etcd key from namespace, environment, and config key"""
        base = f"/config/{namespace}/{environment}"
        return f"{base}/{key}" if key else base
    
    async def get_config(self, namespace: str, environment: str, key: str) -> Optional[ConfigValue]:
        """Get a specific configuration value"""
        if not self.client:
            await self.connect()
        
        etcd_key = self._make_key(namespace, environment, key)
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.client.get, etcd_key
            )
            
            if result[0] is None:
                return None
            
            data = json.loads(result[0].decode('utf-8'))
            return ConfigValue(**data)
            
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in etcd value", key=etcd_key, error=str(e))
            return None
        except Exception as e:
            logger.error("Failed to get config from etcd", key=etcd_key, error=str(e))
            # Try to reconnect on error
            await self._try_reconnect()
            raise
    
    async def get_all_configs(self, namespace: str, environment: str) -> Dict[str, ConfigValue]:
        """Get all configuration values for a namespace/environment"""
        if not self.client:
            await self.connect()
        
        prefix = self._make_key(namespace, environment)
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.client.get_prefix, prefix
            )
            
            configs = {}
            for value, metadata in result:
                if value is None:
                    continue
                
                # Extract the key from the full etcd key
                full_key = metadata.key.decode('utf-8')
                config_key = full_key[len(prefix):].lstrip('/')
                
                try:
                    data = json.loads(value.decode('utf-8'))
                    configs[config_key] = ConfigValue(**data)
                except json.JSONDecodeError as e:
                    logger.error("Invalid JSON in etcd value", 
                               key=full_key, error=str(e))
                    continue
            
            return configs
            
        except Exception as e:
            logger.error("Failed to get configs from etcd", 
                       namespace=namespace, environment=environment, error=str(e))
            await self._try_reconnect()
            raise
    
    async def _try_reconnect(self) -> None:
        """Try to reconnect to etcd on connection failure"""
        try:
            self.client = None
            await self.connect()
        except Exception as e:
            logger.error("Failed to reconnect to etcd", error=str(e))
    
    async def close(self) -> None:
        """Close the etcd connection"""
        if self.client:
            self.client.close()