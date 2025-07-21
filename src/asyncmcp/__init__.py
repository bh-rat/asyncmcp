"""
Async MCP - Async Transport layer for MCP

This package provides async transport layer implementations for MCP (Model Context Protocol)
clients and servers, with support for AWS SQS and SNS as transport mechanisms.
"""

__version__ = "0.1.0"

from asyncmcp.sns_sqs.utils import SnsSqsTransportConfig
from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.server import sns_sqs_server
from asyncmcp.sqs.utils import SqsTransportConfig
from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.server import sqs_server
from asyncmcp.sqs.manager import SqsSessionManager

__all__ = [
    # SNS-SQS Transport
    "SnsSqsTransportConfig",
    "sns_sqs_client", 
    "sns_sqs_server",
    
    # SQS Transport
    "SqsTransportConfig",
    "sqs_client",
    "sqs_server",
    
    # SQS Session Management (NEW)
    "SqsSessionManager",
    
    # Package info
    "__version__",
]
