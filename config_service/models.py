from typing import Optional, Dict, Any, List
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


# Management API Models
class EmergencyOverrideRequest(BaseModel):
    value: Any
    reason: Optional[str] = None
    created_by: Optional[str] = None


class DeployRequest(BaseModel):
    git_ref: str
    created_by: str
    configs: Dict[str, Any]  # key -> value mapping


class VersionHistoryResponse(BaseModel):
    namespace: str
    environment: str
    versions: Dict[str, List[ConfigValue]]


class DeployResponse(BaseModel):
    success: bool
    message: str
    version: str
    deployed_count: int
