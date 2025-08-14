from typing import Optional, Dict, Any
from pydantic import BaseModel
from datetime import datetime


class ConfigMetadata(BaseModel):
    created_by: str
    created_at: datetime
    approved_by: Optional[str] = None
    git_commit: Optional[str] = None


class ConfigValue(BaseModel):
    value: Any
    version: str
    metadata: ConfigMetadata


class ConfigResponse(BaseModel):
    key: str
    value: Any
    version: str
    metadata: ConfigMetadata


class ConfigBatchResponse(BaseModel):
    namespace: str
    environment: str
    configs: Dict[str, ConfigValue]