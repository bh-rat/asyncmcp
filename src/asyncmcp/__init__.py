"""
AsyncMCP: Async transports for Model Context Protocol
"""

from importlib.metadata import version, PackageNotFoundError

from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.server import sqs_server
from asyncmcp.webhook.utils import WebhookServerConfig, WebhookClientConfig
from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.server import webhook_server
from asyncmcp.sqs.server import SqsTransport, sqs_server
from asyncmcp.sqs.utils import SqsServerConfig, SqsClientConfig
from asyncmcp.sqs.manager import SqsSessionManager

from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.server import sns_sqs_server, SnsSqsTransport
from asyncmcp.sns_sqs.manager import SnsSqsSessionManager
from asyncmcp.sns_sqs.utils import SnsSqsServerConfig, SnsSqsClientConfig

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
