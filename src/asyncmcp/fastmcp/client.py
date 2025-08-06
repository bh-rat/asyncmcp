"""FastMCP client utilities for asyncmcp integration.

This module provides utilities to create FastMCP clients that communicate
over asyncmcp's custom transports.
"""

from typing import Any, Dict, Optional, TypeVar, Union

from fastmcp import Client

from asyncmcp.fastmcp.transport import AsyncMCPTransport, TransportType
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.streamable_http_webhook.utils import StreamableHTTPWebhookClientConfig
from asyncmcp.webhook.utils import WebhookClientConfig

# Type variable for the transport
T = TypeVar("T")


def create_fastmcp_client(
    transport_type: TransportType,
    config: Union[SqsClientConfig, SnsSqsClientConfig, WebhookClientConfig, StreamableHTTPWebhookClientConfig],
    low_level_clients: Optional[Dict[str, Any]] = None,
    **client_kwargs: Any,
) -> Client[AsyncMCPTransport]:
    """Create a FastMCP client that communicates over an asyncmcp transport.

    This function creates a FastMCP Client instance configured to use one of
    asyncmcp's custom transports (SQS, SNS+SQS, Webhook, etc.).

    Args:
        transport_type: The type of asyncmcp transport to use
        config: Configuration for the selected transport
        low_level_clients: Low-level clients (e.g., boto3) required by the transport
        **client_kwargs: Additional arguments passed to the FastMCP Client

    Returns:
        Client: A FastMCP client configured for the specified transport

    Example:
        ```python
        from asyncmcp.fastmcp import create_fastmcp_client
        from asyncmcp.sqs.utils import SqsClientConfig
        import boto3

        # Configure SQS transport
        config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/responses",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests"
        )

        # Create client
        sqs_client = boto3.client("sqs")
        client = create_fastmcp_client(
            transport_type="sqs",
            config=config,
            low_level_clients={"sqs_client": sqs_client}
        )

        # Use the client
        async with client:
            tools = await client.list_tools()
            result = await client.call_tool("add", {"a": 1, "b": 2})
        ```
    """
    # Create AsyncMCP transport
    transport = AsyncMCPTransport(
        transport_type=transport_type,
        config=config,
        low_level_clients=low_level_clients or {},
    )

    # Create and return FastMCP client with the transport
    return Client(transport=transport, **client_kwargs)
