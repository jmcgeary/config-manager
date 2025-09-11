import json
import asyncio
import time
from typing import Optional, Dict, Any, List, Callable, Awaitable
from urllib.parse import urlparse
import etcd3
from etcd3.events import PutEvent, DeleteEvent
import structlog
from .models import ConfigValue, ConfigMetadata

logger = structlog.get_logger()


class EtcdClient:
    def __init__(self, endpoints: List[str]):
        self.endpoints = endpoints
        self.client: Optional[etcd3.Etcd3Client] = None
        self._current_endpoint_index = 0
        self._watches: Dict[str, Any] = {}
        self._watch_callbacks: Dict[str, List[Callable]] = {}
    
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
    
    async def watch_prefix(self, prefix: str, callback: Callable[[str, Optional[ConfigValue]], Awaitable[None]]) -> None:
        """Watch for changes to keys with a given prefix"""
        if not self.client:
            await self.connect()
        
        if prefix not in self._watch_callbacks:
            self._watch_callbacks[prefix] = []
        
        self._watch_callbacks[prefix].append(callback)
        
        # Start watching if not already watching this prefix
        if prefix not in self._watches:
            logger.info("Starting watch for prefix", prefix=prefix)
            
            # Use etcd3's watch_prefix method correctly
            # Store the main event loop for callback use
            main_loop = asyncio.get_event_loop()
            
            def watch_callback(event):
                # Schedule the async handler to run in the main event loop from etcd watch thread
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_watch_event(prefix, event), main_loop
                    )
                except Exception as e:
                    logger.error("Error scheduling watch callback", error=str(e))
            
            def start_watch():
                return self.client.add_watch_prefix_callback(prefix, watch_callback)
            
            watch_id = await asyncio.get_event_loop().run_in_executor(None, start_watch)
            self._watches[prefix] = watch_id
    
    async def _handle_watch_event(self, prefix: str, watch_response) -> None:
        """Handle etcd watch events"""
        try:
            # Extract events from the WatchResponse
            for event in watch_response.events:
                key = event.key.decode('utf-8')
                
                if isinstance(event, PutEvent):
                    # Configuration was updated or created
                    try:
                        data = json.loads(event.value.decode('utf-8'))
                        config_value = ConfigValue(**data)
                        logger.info("Config changed", key=key, action="put")
                    except json.JSONDecodeError as e:
                        logger.error("Invalid JSON in watch event", key=key, error=str(e))
                        config_value = None
                elif isinstance(event, DeleteEvent):
                    # Configuration was deleted
                    config_value = None
                    logger.info("Config deleted", key=key, action="delete")
                else:
                    continue
                
                # Notify all callbacks for this prefix
                callbacks = self._watch_callbacks.get(prefix, [])
                for callback in callbacks:
                    try:
                        await callback(key, config_value)
                    except Exception as e:
                        logger.error("Error in watch callback", key=key, error=str(e))
                    
        except Exception as e:
            logger.error("Error handling watch event", prefix=prefix, error=str(e))
    
    def unwatch_prefix(self, prefix: str, callback: Callable) -> None:
        """Remove a callback from watching a prefix"""
        if prefix in self._watch_callbacks:
            try:
                self._watch_callbacks[prefix].remove(callback)
                
                # If no more callbacks, stop watching
                if not self._watch_callbacks[prefix] and prefix in self._watches:
                    watch_id = self._watches.pop(prefix)
                    if self.client:
                        self.client.cancel_watch(watch_id)
                    logger.info("Stopped watching prefix", prefix=prefix)
            except ValueError:
                pass  # Callback wasn't in the list
    
    async def set_config(self, namespace: str, environment: str, key: str, config_value: ConfigValue) -> None:
        """Set a configuration value"""
        if not self.client:
            await self.connect()
        
        etcd_key = self._make_key(namespace, environment, key)
        value_json = json.dumps(config_value.model_dump(mode='json'))
        
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self.client.put, etcd_key, value_json
            )
            logger.info("Config set", key=etcd_key)
        except Exception as e:
            logger.error("Failed to set config in etcd", key=etcd_key, error=str(e))
            await self._try_reconnect()
            raise
    
    async def delete_config(self, namespace: str, environment: str, key: str) -> bool:
        """Delete a configuration value"""
        if not self.client:
            await self.connect()
        
        etcd_key = self._make_key(namespace, environment, key)
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.client.delete, etcd_key
            )
            deleted = result  # Returns True if key was deleted
            logger.info("Config deleted", key=etcd_key, deleted=deleted)
            return deleted
        except Exception as e:
            logger.error("Failed to delete config from etcd", key=etcd_key, error=str(e))
            await self._try_reconnect()
            raise
    
    async def get_all_versions(self, namespace: str, environment: str) -> Dict[str, List[ConfigValue]]:
        """Get version history for all configurations (simplified implementation)"""
        # For now, just return current versions
        # In a full implementation, we'd store version history separately
        configs = await self.get_all_configs(namespace, environment)
        return {key: [config] for key, config in configs.items()}
    
    async def close(self) -> None:
        """Close the etcd connection"""
        # Cancel all watches
        for prefix, watch_id in self._watches.items():
            if self.client:
                self.client.cancel_watch(watch_id)
        self._watches.clear()
        self._watch_callbacks.clear()
        
        if self.client:
            self.client.close()

    async def get_cluster_status(self) -> Dict[str, Any]:
        """Return cluster member health and leader info across configured endpoints.

        Strategy:
        - Connect to the first healthy endpoint to fetch cluster members and the leader ID.
        - Build a map from client URL host:port → member {id,name}.
        - Probe each configured endpoint with status() to mark health and version.
        - Join the results: report {id,name,is_leader,healthy,endpoint,version} for each endpoint.
        """

        def normalize_member_id(val: Any) -> Optional[str]:
            # Normalize etcd member ID to lowercase hex string
            if val is None:
                return None
            # Some clients return a Member object for leader; extract .id
            try:
                if hasattr(val, 'id'):
                    val = getattr(val, 'id')
            except Exception:
                pass
            try:
                if isinstance(val, int):
                    return format(val, 'x')
                # Could be bytes or string hex already
                s = str(val)
                # If it's like "Member 123..." pull digits
                if s.startswith('Member '):
                    # Best-effort extract numeric id between 'Member ' and ':'
                    try:
                        num = int(s.split('Member ')[1].split(':')[0].strip())
                        return format(num, 'x')
                    except Exception:
                        return s
                return s
            except Exception:
                return str(val)

        def url_hostport(u: str) -> Optional[str]:
            try:
                p = urlparse(u)
                host = p.hostname
                port = p.port or 2379
                if host:
                    return f"{host}:{port}"
            except Exception:
                return None
            return None

        loop = asyncio.get_event_loop()

        # Step 1: Find a healthy endpoint and get members + leader ID
        cluster_members: List[Any] = []  # raw Member objects
        leader_hex: Optional[str] = None
        discovery_error: Optional[str] = None
        discovery_client = None
        for endpoint in self.endpoints:
            try:
                p = urlparse(f"http://{endpoint}" if "://" not in endpoint else endpoint)
                host = p.hostname or "localhost"
                port = p.port or 2379
                discovery_client = etcd3.client(host=host, port=port)
                status = await loop.run_in_executor(None, discovery_client.status)
                raw_leader = getattr(status, 'leader', None) if status is not None else None
                if raw_leader is None and isinstance(status, dict):
                    raw_leader = status.get('leader')
                leader_hex = normalize_member_id(raw_leader)
                # Fetch member list from this healthy endpoint
                try:
                    raw_members = await loop.run_in_executor(None, lambda: list(discovery_client.members))
                except TypeError:
                    # Some clients use a method instead of property
                    raw_members = await loop.run_in_executor(None, discovery_client.members)
                    raw_members = list(raw_members)
                cluster_members = raw_members or []
                break
            except Exception as e:
                discovery_error = str(e)
                continue
            finally:
                try:
                    if discovery_client is not None:
                        discovery_client.close()
                except Exception:
                    pass

        # Build map of client host:port → {id,name}
        member_by_hostport: Dict[str, Dict[str, Any]] = {}
        for m in cluster_members:
            try:
                mid = normalize_member_id(getattr(m, 'id', None))
                mname = getattr(m, 'name', None) or ''
                client_urls = getattr(m, 'client_urls', None) or []
                # Some clients expose as method
                if callable(client_urls):
                    try:
                        client_urls = client_urls()
                    except Exception:
                        client_urls = []
                for cu in client_urls:
                    hp = url_hostport(cu)
                    if hp:
                        member_by_hostport[hp] = {'id': mid, 'name': mname}
            except Exception:
                continue

        # Step 2: Probe each configured endpoint for health and version
        results: List[Dict[str, Any]] = []
        for endpoint in self.endpoints:
            client = None
            try:
                parsed = urlparse(f"http://{endpoint}" if "://" not in endpoint else endpoint)
                host = parsed.hostname or "localhost"
                port = parsed.port or 2379
                ep_label = f"{host}:{port}"

                client = etcd3.client(host=host, port=port)
                status = await loop.run_in_executor(None, client.status)
                version = getattr(status, 'version', None)
                if version is None and isinstance(status, dict):
                    version = status.get('version')

                member_meta = member_by_hostport.get(ep_label, {'id': None, 'name': host})
                results.append({
                    'id': member_meta.get('id'),
                    'name': member_meta.get('name') or host,
                    'endpoint': ep_label,
                    'version': version,
                    'healthy': True,
                })
            except Exception as e:
                logger.warning("Failed to get status from endpoint", endpoint=endpoint, error=str(e))
                parsed = urlparse(f"http://{endpoint}" if "://" not in endpoint else endpoint)
                host = parsed.hostname or str(endpoint)
                port = parsed.port or 2379
                ep_label = f"{host}:{port}"
                member_meta = member_by_hostport.get(ep_label, {'id': None, 'name': host})
                results.append({
                    'id': member_meta.get('id'),
                    'name': member_meta.get('name') or host,
                    'endpoint': ep_label,
                    'version': None,
                    'healthy': False,
                })
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass

        # Step 3: mark leader
        for m in results:
            m['is_leader'] = (leader_hex is not None and m['id'] == leader_hex)

        return {
            'leader_id': leader_hex,
            'members': [
                {
                    'id': m['id'],
                    'name': m['name'],
                    'is_leader': m['is_leader'],
                    'healthy': m['healthy'],
                    'endpoint': m['endpoint'],
                    'version': m['version'],
                }
                for m in results
            ]
        }

    async def check_replication(
        self,
        namespace: str,
        environment: str,
        key: str,
        expected_value: Any,
        timeout_ms: int = 3000,
        interval_ms: int = 75,
        sim_down: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Probe each configured endpoint until the written value is observed.

        Returns a list of dicts: { endpoint, ok, elapsed_ms }
        """
        etcd_key = self._make_key(namespace, environment, key)
        results: List[Dict[str, Any]] = []
        loop = asyncio.get_event_loop()

        sim_down_set = set(sim_down or [])
        for ep in self.endpoints:
            parsed = urlparse(f"http://{ep}" if "://" not in ep else ep)
            host = parsed.hostname or "localhost"
            port = parsed.port or 2379
            label = f"{host}:{port}"
            if label in sim_down_set:
                # Skip contacting endpoint; simulate down
                results.append({'endpoint': label, 'ok': False, 'elapsed_ms': 0, 'error': 'simulated down'})
                continue
            client = None
            start = time.time()
            ok = False
            error: Optional[str] = None
            try:
                client = etcd3.client(host=host, port=port)
                # Poll until timeout
                deadline = start + (timeout_ms / 1000.0)
                while time.time() < deadline:
                    try:
                        value, meta = await loop.run_in_executor(None, client.get, etcd_key)
                        if value is not None:
                            try:
                                data = json.loads(value.decode('utf-8'))
                                if data.get('value') == expected_value:
                                    ok = True
                                    break
                            except Exception:
                                pass
                    except Exception as e:
                        error = str(e)
                        await asyncio.sleep(interval_ms / 1000.0)
                        continue
                    await asyncio.sleep(interval_ms / 1000.0)
            except Exception as e:
                error = str(e)
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
            elapsed_ms = int((time.time() - start) * 1000)
            results.append({
                'endpoint': label,
                'ok': ok,
                'elapsed_ms': elapsed_ms,
                **({'error': error} if error and not ok else {})
            })

        return results
