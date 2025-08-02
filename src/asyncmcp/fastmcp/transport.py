"""AsyncMCP transport adapter for FastMCP.

This module provides a ClientTransport implementation that bridges FastMCP
servers with asyncmcp's custom transports.
"""

import contextlib
import logging
from typing import Any, AsyncIterator, Callable, Dict, Literal, Optional, Union

from fastmcp.client.transports import ClientTransport, SessionKwargs
from mcp.client.session import ClientSession
from typing_extensions import Unpack

from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.streamable_http_webhook.client import streamable_http_webhook_client
from asyncmcp.streamable_http_webhook.utils import StreamableHTTPWebhookClientConfig
from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.utils import WebhookClientConfig

logger = logging.getLogger(__name__)

TransportType = Literal["sqs", "sns_sqs", "webhook", "streamable_http_webhook"]


class AsyncMCPTransport(ClientTransport):
    """A FastMCP ClientTransport that uses asyncmcp's async transports.

    This transport adapter allows FastMCP servers to communicate over
    asyncmcp's custom transports (SQS, SNS+SQS, Webhook, etc.) by
    implementing FastMCP's ClientTransport protocol.
    """

    def __init__(
        self,
        transport_type: TransportType,
        config: Union[SqsClientConfig, SnsSqsClientConfig, WebhookClientConfig, StreamableHTTPWebhookClientConfig],
        low_level_clients: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the AsyncMCP transport adapter.

        Args:
            transport_type: The type of asyncmcp transport to use
            config: Configuration for the selected transport
            low_level_clients: Low-level clients (e.g., boto3 clients) required by the transport
        """
        self.transport_type = transport_type
        self.config = config
        self.low_level_clients = low_level_clients or {}

        # Map transport types to their client functions
        self._client_factories: Dict[TransportType, Callable[..., Any]] = {
            "sqs": self._create_sqs_client,
            "sns_sqs": self._create_sns_sqs_client,
            "webhook": self._create_webhook_client,
            "streamable_http_webhook": self._create_streamable_http_webhook_client,
        }

        if transport_type not in self._client_factories:
            raise ValueError(f"Unsupported transport type: {transport_type}")

    @contextlib.asynccontextmanager
    async def connect_session(self, **session_kwargs: Unpack[SessionKwargs]) -> AsyncIterator[ClientSession]:
        """Connect to the asyncmcp transport and yield a ClientSession.

        This method implements the FastMCP ClientTransport protocol by
        establishing a connection to the asyncmcp transport and providing
        a ClientSession for communication.

        Args:
            **session_kwargs: Additional kwargs passed to ClientSession

        Yields:
            ClientSession: An active MCP client session
        """
        # Get the appropriate client factory
        client_factory = self._client_factories[self.transport_type]  # type: ignore[index]

        # Create and connect to the asyncmcp transport
        async with client_factory() as (read_stream, write_stream):
            # Create ClientSession with the streams
            async with ClientSession(read_stream=read_stream, write_stream=write_stream, **session_kwargs) as session:
                yield session

    @contextlib.asynccontextmanager
    async def _create_sqs_client(self):
        """Create an SQS client connection."""
        if "sqs_client" not in self.low_level_clients:
            raise ValueError("SQS transport requires 'sqs_client' in low_level_clients")

        async with sqs_client(
            config=self.config,  # type: ignore[arg-type]
            sqs_client=self.low_level_clients["sqs_client"],
        ) as (read_stream, write_stream):
            yield read_stream, write_stream

    @contextlib.asynccontextmanager
    async def _create_sns_sqs_client(self):
        """Create an SNS+SQS client connection."""
        if "sqs_client" not in self.low_level_clients:
            raise ValueError("SNS+SQS transport requires 'sqs_client' in low_level_clients")
        if "sns_client" not in self.low_level_clients:
            raise ValueError("SNS+SQS transport requires 'sns_client' in low_level_clients")

        async with sns_sqs_client(
            config=self.config,  # type: ignore[arg-type]
            sqs_client=self.low_level_clients["sqs_client"],
            sns_client=self.low_level_clients["sns_client"],
        ) as (read_stream, write_stream):
            yield read_stream, write_stream

    @contextlib.asynccontextmanager
    async def _create_webhook_client(self):
        """Create a Webhook client connection."""
        async with webhook_client(config=self.config) as streams:  # type: ignore[arg-type]
            # webhook_client returns a 3-tuple, we only need the first two
            yield streams[0], streams[1]

    @contextlib.asynccontextmanager
    async def _create_streamable_http_webhook_client(self):
        """Create a Streamable HTTP + Webhook client connection."""
        async with streamable_http_webhook_client(config=self.config) as streams:  # type: ignore[arg-type]
            # streamable_http_webhook_client returns a 3-tuple, we only need the first two
            yield streams[0], streams[1]

    async def close(self):
        """Close the transport.

        This is called by FastMCP when cleaning up resources.
        """
        # The actual cleanup happens in the context manager
        pass

    def __repr__(self) -> str:
        return f"<AsyncMCPTransport(type='{self.transport_type}')>"
