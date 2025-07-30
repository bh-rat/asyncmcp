"""
AsyncMCP: Async transports for Model Context Protocol
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version(__name__.split(".")[0])
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]

# Conditional imports for SQS transport
try:
    import boto3  # noqa: F401

    from asyncmcp.sqs.client import sqs_client
    from asyncmcp.sqs.manager import SqsSessionManager
    from asyncmcp.sqs.server import SqsTransport, sqs_server
    from asyncmcp.sqs.utils import SqsClientConfig, SqsServerConfig

    __all__.extend(
        [
            "sqs_client",
            "sqs_server",
            "SqsTransport",
            "SqsServerConfig",
            "SqsClientConfig",
            "SqsSessionManager",
        ]
    )
except ImportError:
    pass

# Conditional imports for SNS+SQS transport
try:
    import boto3  # noqa: F401

    from asyncmcp.sns_sqs.client import sns_sqs_client
    from asyncmcp.sns_sqs.manager import SnsSqsSessionManager
    from asyncmcp.sns_sqs.server import SnsSqsTransport, sns_sqs_server
    from asyncmcp.sns_sqs.utils import SnsSqsClientConfig, SnsSqsServerConfig

    __all__.extend(
        [
            "sns_sqs_client",
            "sns_sqs_server",
            "SnsSqsTransport",
            "SnsSqsSessionManager",
            "SnsSqsServerConfig",
            "SnsSqsClientConfig",
        ]
    )
except ImportError:
    pass

# Conditional imports for Webhook transport
try:
    import httpx  # noqa: F401
    import orjson  # noqa: F401
    import starlette  # noqa: F401
    import uvicorn  # noqa: F401

    from asyncmcp.webhook.client import webhook_client
    from asyncmcp.webhook.server import webhook_server
    from asyncmcp.webhook.utils import WebhookClientConfig, WebhookServerConfig

    __all__.extend(
        [
            "WebhookServerConfig",
            "WebhookClientConfig",
            "webhook_client",
            "webhook_server",
        ]
    )
except ImportError:
    pass

# Conditional imports for StreamableHTTP + Webhook transport
try:
    import httpx  # noqa: F401
    import httpx_sse  # noqa: F401
    import orjson  # noqa: F401
    import starlette  # noqa: F401
    import uvicorn  # noqa: F401

    from asyncmcp.streamable_http_webhook.client import streamable_http_webhook_client
    from asyncmcp.streamable_http_webhook.manager import StreamableHTTPWebhookSessionManager
    from asyncmcp.streamable_http_webhook.server import StreamableHTTPWebhookTransport
    from asyncmcp.streamable_http_webhook.utils import (
        StreamableHTTPWebhookClientConfig,
        StreamableHTTPWebhookConfig,
        webhook_tool,
    )

    __all__.extend(
        [
            "streamable_http_webhook_client",
            "StreamableHTTPWebhookSessionManager",
            "StreamableHTTPWebhookTransport",
            "StreamableHTTPWebhookConfig",
            "StreamableHTTPWebhookClientConfig",
            "webhook_tool",
        ]
    )
except ImportError:
    pass


def _check_transport_dependencies(transport: str) -> None:
    """Check if dependencies for a specific transport are installed."""
    error_messages = {
        "sqs": "SQS transport requires boto3. Install with: pip install asyncmcp[sqs]",
        "sns_sqs": "SNS+SQS transport requires boto3. Install with: pip install asyncmcp[sns_sqs]",
        "webhook": "Webhook transport requires httpx, starlette, uvicorn, and orjson. "
        "Install with: pip install asyncmcp[webhook]",
        "streamable_http_webhook": "StreamableHTTP+Webhook transport requires httpx, httpx-sse, starlette, uvicorn, "
        "and orjson. Install with: pip install asyncmcp[streamable_http_webhook]",
    }

    if transport in ["sqs", "sns_sqs"]:
        try:
            import boto3  # noqa: F401
        except ImportError:
            raise ImportError(error_messages[transport])

    elif transport == "webhook":
        missing = []
        for module in ["httpx", "starlette", "uvicorn", "orjson"]:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)
        if missing:
            raise ImportError(error_messages[transport])

    elif transport == "streamable_http_webhook":
        missing = []
        for module in ["httpx", "httpx_sse", "starlette", "uvicorn", "orjson"]:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)
        if missing:
            raise ImportError(error_messages[transport])
