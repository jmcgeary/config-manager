import os
import time
import json
from typing import Any, Optional, Dict
import httpx
import structlog

logger = structlog.get_logger()


class ConfigClient:
    """
    Client library for accessing configuration from the config service sidecar.
    
    Features:
    - Automatic connection to localhost sidecar
    - Local caching with TTL for resilience
    - Error handling and graceful degradation
    - Simple get() method for configuration access
    - Sidecar handles all real-time updates automatically
    """
    
    def __init__(
        self, 
        base_url: str = None,
        namespace: str = None,
        environment: str = None,
        cache_ttl: int = 30,  # Shorter TTL since sidecar has real-time updates
        timeout: int = 5
    ):
        self.base_url = base_url or os.getenv('CONFIG_SERVICE_URL', 'http://localhost:8080')
        self.namespace = namespace or os.getenv('SERVICE_NAME', 'default')
        self.environment = environment or os.getenv('ENVIRONMENT', 'development')
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        
        logger.info("ConfigClient initialized", 
                   namespace=self.namespace, 
                   environment=self.environment,
                   base_url=self.base_url)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value.
        
        Args:
            key: Configuration key (e.g., "database.pool_size")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        # Check cache first
        cache_key = f"{self.namespace}:{self.environment}:{key}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached
        
        # Fetch from service
        try:
            url = f"/v1/config/{self.namespace}/{self.environment}/{key}"
            response = self._client.get(url)
            
            if response.status_code == 404:
                logger.debug("Configuration key not found", key=key)
                self._set_cache(cache_key, default)
                return default
            
            response.raise_for_status()
            
            data = response.json()
            value = data.get('value', default)
            
            # Cache the result
            self._set_cache(cache_key, value)
            
            logger.debug("Retrieved configuration", key=key, value=value)
            return value
            
        except httpx.TimeoutException:
            logger.warning("Timeout getting config, using cache or default", key=key)
            return self._get_cached_or_default(cache_key, default)
        except httpx.ConnectError:
            logger.warning("Failed to connect to config service, using cache or default", key=key)
            return self._get_cached_or_default(cache_key, default)
        except Exception as e:
            logger.error("Error getting config", key=key, error=str(e))
            return self._get_cached_or_default(cache_key, default)
    
    def get_all(self) -> Dict[str, Any]:
        """
        Get all configuration values for this service.
        
        Returns:
            Dictionary of all configuration key-value pairs
        """
        cache_key = f"{self.namespace}:{self.environment}:*"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached
        
        try:
            url = f"/v1/config/{self.namespace}/{self.environment}"
            response = self._client.get(url)
            response.raise_for_status()
            
            data = response.json()
            configs = data.get('configs', {})
            
            # Extract just the values
            result = {k: v.get('value') for k, v in configs.items()}
            
            # Cache the result
            self._set_cache(cache_key, result)
            
            logger.debug("Retrieved all configurations", count=len(result))
            return result
            
        except Exception as e:
            logger.error("Error getting all configs", error=str(e))
            return self._get_cached_or_default(cache_key, {})
    
    def get_int(self, key: str, default: int = None, min_val: int = None, max_val: int = None) -> int:
        """Get an integer configuration value with optional validation."""
        value = self.get(key, default)
        
        if value is None:
            if default is not None:
                return default
            raise ValueError(f"Configuration key '{key}' not found and no default provided")
        
        try:
            int_value = int(value)
            
            if min_val is not None and int_value < min_val:
                raise ValueError(f"Value {int_value} is below minimum {min_val}")
            if max_val is not None and int_value > max_val:
                raise ValueError(f"Value {int_value} is above maximum {max_val}")
            
            return int_value
        except (ValueError, TypeError) as e:
            logger.error("Invalid integer value", key=key, value=value, error=str(e))
            if default is not None:
                return default
            raise
    
    def get_bool(self, key: str, default: bool = None) -> bool:
        """Get a boolean configuration value."""
        value = self.get(key, default)
        
        if value is None:
            if default is not None:
                return default
            raise ValueError(f"Configuration key '{key}' not found and no default provided")
        
        if isinstance(value, bool):
            return value
        
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        
        return bool(value)
    
    def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        if cache_key not in self._cache:
            return None
        
        entry = self._cache[cache_key]
        if time.time() - entry['timestamp'] > self.cache_ttl:
            del self._cache[cache_key]
            return None
        
        return entry['value']
    
    def _set_cache(self, cache_key: str, value: Any) -> None:
        """Set value in cache with timestamp."""
        self._cache[cache_key] = {
            'value': value,
            'timestamp': time.time()
        }
    
    def _get_cached_or_default(self, cache_key: str, default: Any) -> Any:
        """Get from cache ignoring expiry, or return default."""
        if cache_key in self._cache:
            return self._cache[cache_key]['value']
        return default
    
    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()