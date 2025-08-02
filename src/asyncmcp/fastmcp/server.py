"""FastMCP server utilities for asyncmcp integration.

This module provides utilities to run FastMCP servers over asyncmcp's
custom transports.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

import anyio
from fastmcp import FastMCP

if TYPE_CHECKING:
    pass

from asyncmcp.fastmcp.transport import TransportType
from asyncmcp.sns_sqs.manager import SnsSqsSessionManager
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig, SnsSqsServerConfig
from asyncmcp.sqs.manager import SqsSessionManager
from asyncmcp.sqs.utils import SqsClientConfig, SqsServerConfig
from asyncmcp.streamable_http_webhook.manager import StreamableHTTPWebhookSessionManager
from asyncmcp.streamable_http_webhook.utils import (
    StreamableHTTPWebhookClientConfig,
    StreamableHTTPWebhookConfig,
)
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp.webhook.utils import WebhookClientConfig, WebhookServerConfig

logger = logging.getLogger(__name__)


async def run_fastmcp_server(
    mcp: FastMCP[Any],
    transport_type: TransportType,
    server_config: Union[
        SqsServerConfig,
        SnsSqsServerConfig,
        WebhookServerConfig,
        StreamableHTTPWebhookConfig,
    ],
    client_config: Union[
        SqsClientConfig,
        SnsSqsClientConfig,
        WebhookClientConfig,
        StreamableHTTPWebhookClientConfig,
    ],
    low_level_clients: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> None:
    """Run a FastMCP server over an asyncmcp transport.

    This function creates a proxy of the FastMCP server that communicates
    through asyncmcp's custom transports, then runs the appropriate session
    manager to handle incoming requests.

    Args:
        mcp: The FastMCP server instance to run
        transport_type: The type of asyncmcp transport to use
        server_config: Server-side configuration for the transport
        client_config: Client-side configuration for the transport proxy
        low_level_clients: Low-level clients (e.g., boto3) required by the transport
        **kwargs: Additional arguments passed to the session manager

    Example:
        ```python
        from fastmcp import FastMCP
        from asyncmcp.fastmcp import run_fastmcp_server
        from asyncmcp.sqs.utils import SqsServerConfig, SqsClientConfig
        import boto3

        # Create FastMCP server
        mcp = FastMCP("my-server")

        @mcp.tool()
        def add(a: int, b: int) -> int:
            return a + b

        # Configure transport
        server_config = SqsServerConfig(
            request_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests"
        )
        client_config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/responses"
        )

        # Run server
        sqs_client = boto3.client("sqs")
        await run_fastmcp_server(
            mcp,
            transport_type="sqs",
            server_config=server_config,
            client_config=client_config,
            low_level_clients={"sqs_client": sqs_client}
        )
        ```
    """
    low_level_clients = low_level_clients or {}

    # Get the underlying MCP server from the FastMCP instance
    mcp_server = mcp._mcp_server  # type: ignore[attr-defined]

    # Run the appropriate session manager based on transport type
    if transport_type == "sqs":
        if "sqs_client" not in low_level_clients:
            raise ValueError("SQS transport requires 'sqs_client' in low_level_clients")

        manager = SqsSessionManager(
            app=mcp_server,
            config=server_config,  # type: ignore[arg-type]
            sqs_client=low_level_clients["sqs_client"],
            **kwargs,
        )

    elif transport_type == "sns_sqs":
        if "sqs_client" not in low_level_clients:
            raise ValueError("SNS+SQS transport requires 'sqs_client' in low_level_clients")
        if "sns_client" not in low_level_clients:
            raise ValueError("SNS+SQS transport requires 'sns_client' in low_level_clients")

        manager = SnsSqsSessionManager(
            app=mcp_server,
            config=server_config,  # type: ignore[arg-type]
            sqs_client=low_level_clients["sqs_client"],
            sns_client=low_level_clients["sns_client"],
            **kwargs,
        )

    elif transport_type == "webhook":
        manager = WebhookSessionManager(
            app=mcp_server,
            config=server_config,  # type: ignore[arg-type]
            **kwargs,
        )

    elif transport_type == "streamable_http_webhook":
        manager = StreamableHTTPWebhookSessionManager(
            app=mcp_server,
            config=server_config,  # type: ignore[arg-type]
            **kwargs,
        )

    else:
        raise ValueError(f"Unsupported transport type: {transport_type}")

    # Run the session manager
    async with manager.run():
        logger.info(f"FastMCP server '{mcp.name}' running on {transport_type} transport")
        # Keep the server running
        await anyio.Event().wait()
