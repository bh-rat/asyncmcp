import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SqsServerConfig:
    """Configuration for SQS server transport."""

    # Queue configuration
    read_queue_url: str  # Server reads requests from this queue

    # Message handling configuration
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    poll_interval_seconds: float = 5.0

    # Optional configurations
    message_attributes: Optional[Dict[str, Any]] = None
    transport_timeout_seconds: Optional[float] = None


@dataclass
class SqsClientConfig:
    """Configuration for SQS client transport."""

    # Queue configuration
    read_queue_url: str  # Client sends requests to this queue (server's read queue)
    response_queue_url: str  # Client receives responses on this queue

    # Client identification
    client_id: Optional[str] = None

    # Message handling configuration (needed for compatibility with common utilities)
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    poll_interval_seconds: float = 5.0

    # Optional configurations
    message_attributes: Optional[Dict[str, Any]] = None
    transport_timeout_seconds: Optional[float] = None

    def __post_init__(self):
        """Initialize client_id if not provided."""
        if self.client_id is None:
            self.client_id = str(uuid.uuid4())
