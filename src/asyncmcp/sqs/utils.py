from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SqsTransportConfig:
    """Configuration for SQS only transport."""

    read_queue_url: str
    response_queue_url: Optional[str] = None
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    message_attributes: Optional[Dict[str, Any]] = None
    poll_interval_seconds: float = 5.0
    client_id: Optional[str] = None
    transport_timeout_seconds: Optional[float] = None
