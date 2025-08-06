"""Proxy utilities and configuration.

This module provides configuration classes and utility functions for the
asyncmcp proxy server.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional, Union

if TYPE_CHECKING:
    from .server import ProxyServer

from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.streamable_http_webhook.utils import StreamableHTTPWebhookClientConfig
from asyncmcp.webhook.utils import WebhookClientConfig

logger = logging.getLogger(__name__)

# Supported backend transport types
BackendTransportType = Literal["sqs", "sns_sqs", "webhook", "streamable_http_webhook"]

# Union type for all backend configurations
BackendConfig = Union[
    SqsClientConfig,
    SnsSqsClientConfig,
    WebhookClientConfig,
    StreamableHTTPWebhookClientConfig,
]


@dataclass
class ProxyConfig:
    """Configuration for the asyncmcp proxy server.

    The proxy server exposes a StreamableHTTP endpoint that forwards
    requests to a configured asyncmcp backend transport.
    """

    # Server settings
    host: str = "127.0.0.1"
    port: int = 8080
    server_path: str = "/mcp"

    # Backend transport settings
    backend_transport: BackendTransportType = "sqs"
    backend_config: Optional[BackendConfig] = None
    backend_clients: Dict[str, Any] = field(default_factory=lambda: {})

    # Session management
    session_timeout: float = 300.0  # 5 minutes
    max_sessions: int = 100
    stateless: bool = False

    # Performance settings
    connection_pool_size: int = 10
    request_timeout: float = 180.0

    # Security settings
    cors_origins: Optional[list[str]] = None
    auth_enabled: bool = False
    auth_token: Optional[str] = None

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.backend_config is None:
            raise ValueError("backend_config is required")

        # Validate backend transport type matches config type
        config_type_map = {
            "sqs": SqsClientConfig,
            "sns_sqs": SnsSqsClientConfig,
            "webhook": WebhookClientConfig,
            "streamable_http_webhook": StreamableHTTPWebhookClientConfig,
        }

        expected_type = config_type_map.get(self.backend_transport)
        if expected_type and not isinstance(self.backend_config, expected_type):  # type: ignore[arg-type]
            raise ValueError(
                f"backend_config must be {expected_type.__name__} for transport type '{self.backend_transport}'"
            )


def create_proxy_server(
    backend_transport: BackendTransportType,
    backend_config: BackendConfig,
    backend_clients: Optional[Dict[str, Any]] = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    server_path: str = "/mcp",
    **kwargs: Any,
) -> "ProxyServer":
    """Create a proxy server with the specified configuration.

    This is a convenience function for creating a proxy server with
    common settings.

    Args:
        backend_transport: The type of backend transport to use
        backend_config: Configuration for the backend transport
        backend_clients: Low-level clients (e.g., boto3) for the backend
        host: Host to bind the proxy server to
        port: Port to bind the proxy server to
        server_path: HTTP path for the MCP endpoint
        **kwargs: Additional configuration options

    Returns:
        ProxyServer: Configured proxy server instance

    Example:
        ```python
        from asyncmcp.proxy import create_proxy_server
        from asyncmcp.sqs.utils import SqsClientConfig
        import boto3

        # Create SQS backend config
        backend_config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/responses"
        )

        # Create proxy server
        proxy = create_proxy_server(
            backend_transport="sqs",
            backend_config=backend_config,
            backend_clients={"sqs_client": boto3.client("sqs")},
            port=8080
        )

        # Run the server
        await proxy.run()
        ```
    """
    from .server import ProxyServer

    config = ProxyConfig(
        host=host,
        port=port,
        server_path=server_path,
        backend_transport=backend_transport,
        backend_config=backend_config,
        backend_clients=backend_clients or {},
        **kwargs,
    )

    return ProxyServer(config)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return str(uuid.uuid4())


def validate_auth_token(token: Optional[str], expected_token: Optional[str]) -> bool:
    """Validate an authentication token.

    Args:
        token: The token to validate
        expected_token: The expected token value

    Returns:
        bool: True if the token is valid, False otherwise
    """
    if not expected_token:
        # No auth required
        return True

    if not token:
        # Auth required but no token provided
        return False

    # Simple token comparison (could be enhanced with JWT, etc.)
    return token == expected_token
