"""
Async MCP - Async Transport layer for MCP

This package provides async transport layer implementations for MCP (Model Context Protocol)
clients and servers, with support for AWS SQS and SNS as transport mechanisms.
"""

__version__ = "0.1.0"

# Import main transport configurations and functions
from asyncmcp.sns_sqs.utils import SnsSqsTransportConfig
from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.server import sns_sqs_server

__all__ = [
    "SnsSqsTransportConfig",
    "sns_sqs_client",
    "sns_sqs_server",
    "__version__",
]
