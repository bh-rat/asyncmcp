"""
Utilities for SNS/SQS transport functionality.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SnsSqsServerConfig:
    """Configuration for SNS/SQS server with session manager."""

    sqs_queue_url: str
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    message_attributes: Optional[Dict[str, Any]] = None
    poll_interval_seconds: float = 5.0
    transport_timeout_seconds: Optional[float] = None

    def __post_init__(self):
        """Validate server configuration."""
        if not self.sqs_queue_url:
            raise ValueError("sqs_queue_url is required for server configuration")


@dataclass
class SnsSqsClientConfig:
    """Configuration for SNS/SQS client."""

    sqs_queue_url: str
    sns_topic_arn: str
    client_id: Optional[str] = None
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    message_attributes: Optional[Dict[str, Any]] = None
    poll_interval_seconds: float = 5.0
    transport_timeout_seconds: Optional[float] = None

    def __post_init__(self):
        """Validate client configuration."""
        if not self.sqs_queue_url:
            raise ValueError("sqs_queue_url is required for client configuration")
        if not self.sns_topic_arn:
            raise ValueError("sns_topic_arn is required for client configuration")
