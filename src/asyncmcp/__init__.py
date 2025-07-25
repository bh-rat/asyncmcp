"""
AsyncMCP: Async transports for Model Context Protocol
"""

from importlib.metadata import PackageNotFoundError, version

from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.manager import SnsSqsSessionManager
from asyncmcp.sns_sqs.server import SnsSqsTransport, sns_sqs_server
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig, SnsSqsServerConfig
from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.manager import SqsSessionManager
from asyncmcp.sqs.server import SqsTransport, sqs_server
from asyncmcp.sqs.utils import SqsClientConfig, SqsServerConfig
from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.server import webhook_server
from asyncmcp.webhook.utils import WebhookClientConfig, WebhookServerConfig

try:
    __version__ = version(__name__.split(".")[0])
except PackageNotFoundError:
    __version__ = "0.0.0"


__all__ = [
    # SQS Transport
    "sqs_client",
    "sqs_server",
    "WebhookServerConfig",
    "WebhookClientConfig",
    "webhook_client",
    "webhook_server",
    "__version__",
    "SqsTransport",
    "SqsServerConfig",
    "SqsClientConfig",
    "SqsSessionManager",
    # SNS+SQS Transport
    "sns_sqs_client",
    "sns_sqs_server",
    "SnsSqsTransport",
    "SnsSqsSessionManager",
    "SnsSqsServerConfig",
    "SnsSqsClientConfig",
]
