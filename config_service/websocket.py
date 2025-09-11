import asyncio
import json
from typing import Dict, Set, Optional
from fastapi import WebSocket, WebSocketDisconnect
import structlog
from .etcd_client import EtcdClient
from .models import ConfigValue

logger = structlog.get_logger()


class WebSocketManager:
    """Manages WebSocket connections for real-time config updates"""
    
    def __init__(self, etcd_client: EtcdClient):
        self.etcd_client = etcd_client
        self.connections: Dict[str, Set[WebSocket]] = {}  # namespace:environment -> websockets
        self.client_subscriptions: Dict[WebSocket, Set[str]] = {}  # websocket -> subscribed prefixes
        self._setup_watches()
    
    def _setup_watches(self) -> None:
        """Set up etcd watches for all config prefixes"""
        # Watch the entire /config prefix to catch all changes
        asyncio.create_task(self._setup_config_watch())
    
    async def _setup_config_watch(self) -> None:
        """Set up watch for all configuration changes"""
        try:
            await self.etcd_client.watch_prefix("/config", self._handle_config_change)
            logger.info("WebSocket manager watching /config for changes")
        except Exception as e:
            logger.error("Failed to setup config watch", error=str(e))
    
    async def _handle_config_change(self, key: str, config_value: Optional[ConfigValue]) -> None:
        """Handle configuration changes from etcd"""
        try:
            # Parse the key to extract namespace and environment
            # Key format: /config/{namespace}/{environment}/{config_key}
            parts = key.strip('/').split('/')
            if len(parts) < 3 or parts[0] != 'config':
                return
            
            namespace = parts[1]
            environment = parts[2]
            config_key = '/'.join(parts[3:]) if len(parts) > 3 else ''
            
            # Determine the subscription key
            subscription_key = f"{namespace}:{environment}"
            
            # Prepare the message
            if config_value is not None:
                message = {
                    "type": "config_change",
                    "namespace": namespace,
                    "environment": environment,
                    "key": config_key,
                    "value": config_value.value,
                    "version": config_value.version,
                    # Serialize Pydantic model to JSON-friendly dict
                    "metadata": config_value.metadata.model_dump(mode="json"),
                }
            else:
                message = {
                    "type": "config_delete",
                    "namespace": namespace,
                    "environment": environment,
                    "key": config_key
                }
            
            # Send to all subscribers
            await self._broadcast_to_subscribers(subscription_key, message)
            
        except Exception as e:
            logger.error("Error handling config change", key=key, error=str(e))
    
    async def _broadcast_to_subscribers(self, subscription_key: str, message: Dict) -> None:
        """Broadcast message to all WebSocket connections subscribed to a key"""
        if subscription_key not in self.connections:
            return
        
        message_json = json.dumps(message)
        disconnected_clients = []
        
        for websocket in self.connections[subscription_key].copy():
            try:
                await websocket.send_text(message_json)
                logger.debug("Sent config change to client", subscription=subscription_key)
            except Exception as e:
                logger.warning("Failed to send to WebSocket client", 
                             subscription=subscription_key, error=str(e))
                disconnected_clients.append(websocket)
        
        # Clean up disconnected clients
        for websocket in disconnected_clients:
            await self._remove_client(websocket)
    
    async def connect(self, websocket: WebSocket, namespace: str, environment: str) -> None:
        """Connect a new WebSocket client"""
        await websocket.accept()
        
        subscription_key = f"{namespace}:{environment}"
        
        # Add to connections
        if subscription_key not in self.connections:
            self.connections[subscription_key] = set()
        self.connections[subscription_key].add(websocket)
        
        # Track client subscriptions
        if websocket not in self.client_subscriptions:
            self.client_subscriptions[websocket] = set()
        self.client_subscriptions[websocket].add(subscription_key)
        
        logger.info("WebSocket client connected", 
                   namespace=namespace, environment=environment, 
                   total_connections=len(self.connections[subscription_key]))
        
        # Send initial connection confirmation
        await websocket.send_text(json.dumps({
            "type": "connected",
            "namespace": namespace,
            "environment": environment,
            "message": "Connected to config updates"
        }))
    
    async def disconnect(self, websocket: WebSocket) -> None:
        """Disconnect a WebSocket client"""
        await self._remove_client(websocket)
    
    async def _remove_client(self, websocket: WebSocket) -> None:
        """Remove a client from all subscriptions"""
        if websocket not in self.client_subscriptions:
            return
        
        # Remove from all subscriptions
        for subscription_key in self.client_subscriptions[websocket]:
            if subscription_key in self.connections:
                self.connections[subscription_key].discard(websocket)
                if not self.connections[subscription_key]:
                    # No more clients for this subscription
                    del self.connections[subscription_key]
        
        # Remove client tracking
        del self.client_subscriptions[websocket]
        
        logger.info("WebSocket client disconnected")
    
    async def handle_websocket(self, websocket: WebSocket, namespace: str, environment: str) -> None:
        """Handle a WebSocket connection lifecycle"""
        try:
            await self.connect(websocket, namespace, environment)
            
            # Keep connection alive and handle messages
            while True:
                try:
                    # We can receive ping/pong or other control messages
                    data = await websocket.receive_text()
                    # For now, we just acknowledge any messages
                    message = json.loads(data)
                    if message.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                except WebSocketDisconnect:
                    break
                except Exception as e:
                    logger.error("Error handling WebSocket message", error=str(e))
                    break
                    
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("WebSocket connection error", error=str(e))
        finally:
            await self.disconnect(websocket)
