"""Proxy client for connecting to asyncmcp backend transports.

This module provides a client adapter that connects to various asyncmcp
backend transports and provides a unified interface for the proxy server.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional, Tuple, Union

from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage

from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.streamable_http_webhook.client import streamable_http_webhook_client
from asyncmcp.streamable_http_webhook.utils import StreamableHTTPWebhookClientConfig
from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.utils import WebhookClientConfig

from .utils import BackendConfig, BackendTransportType

logger = logging.getLogger(__name__)


class ProxyClient:
    """Client adapter for connecting to asyncmcp backend transports.

    This class provides a unified interface for connecting to different
    asyncmcp transports from the proxy server. It handles transport-specific
    connection logic and provides consistent stream interfaces.
    """

    def __init__(
        self,
        transport_type: BackendTransportType,
        config: BackendConfig,
        low_level_clients: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ):
        """Initialize the proxy client.

        Args:
            transport_type: The type of backend transport to connect to
            config: Configuration for the backend transport
            low_level_clients: Low-level clients (e.g., boto3) required by transport
            session_id: Optional session ID for tracking
        """
        self.transport_type = transport_type
        self.config = config
        self.low_level_clients = low_level_clients or {}
        self.session_id = session_id

    @asynccontextmanager
    async def connect(
        self,
    ) -> AsyncIterator[
        Tuple[
            MemoryObjectReceiveStream[Union[SessionMessage, Exception]],
            MemoryObjectSendStream[SessionMessage],
        ]
    ]:
        """Connect to the backend transport and yield streams.

        This method establishes a connection to the configured backend
        transport and yields read/write streams for communication.

        Yields:
            Tuple of (read_stream, write_stream) for bidirectional communication
        """
        if self.transport_type == "sqs":
            async with self._connect_sqs() as streams:
                yield streams

        elif self.transport_type == "sns_sqs":
            async with self._connect_sns_sqs() as streams:
                yield streams

        elif self.transport_type == "webhook":
            async with self._connect_webhook() as streams:
                yield streams

        elif self.transport_type == "streamable_http_webhook":
            async with self._connect_streamable_http_webhook() as streams:
                yield streams

        else:
            raise ValueError(f"Unsupported transport type: {self.transport_type}")

    @asynccontextmanager
    async def _connect_sqs(self):
        """Connect to SQS backend."""
        if "sqs_client" not in self.low_level_clients:
            raise ValueError("SQS transport requires 'sqs_client' in low_level_clients")

        config = self.config
        if not isinstance(config, SqsClientConfig):
            raise TypeError(f"Expected SqsClientConfig, got {type(config)}")

        async with sqs_client(
            config=config,
            sqs_client=self.low_level_clients["sqs_client"],
        ) as (read_stream, write_stream):
            yield read_stream, write_stream

    @asynccontextmanager
    async def _connect_sns_sqs(self):
        """Connect to SNS+SQS backend."""
        if "sqs_client" not in self.low_level_clients:
            raise ValueError("SNS+SQS transport requires 'sqs_client' in low_level_clients")
        if "sns_client" not in self.low_level_clients:
            raise ValueError("SNS+SQS transport requires 'sns_client' in low_level_clients")

        config = self.config
        if not isinstance(config, SnsSqsClientConfig):
            raise TypeError(f"Expected SnsSqsClientConfig, got {type(config)}")

        async with sns_sqs_client(
            config=config,
            sqs_client=self.low_level_clients["sqs_client"],
            sns_client=self.low_level_clients["sns_client"],
            client_topic_arn=config.sns_topic_arn,  # Use the topic from config
        ) as (read_stream, write_stream):
            logger.debug(f"ProxyClient[{self.session_id}]: Connected to SNS+SQS backend")
            yield read_stream, write_stream

    @asynccontextmanager
    async def _connect_webhook(self):
        """Connect to Webhook backend."""
        config = self.config
        if not isinstance(config, WebhookClientConfig):
            raise TypeError(f"Expected WebhookClientConfig, got {type(config)}")

        async with webhook_client(config=config) as streams:
            # webhook_client returns a 3-tuple, we only need the first two
            read_stream, write_stream = streams[0], streams[1]
            logger.debug(f"ProxyClient[{self.session_id}]: Connected to Webhook backend")
            yield read_stream, write_stream

    @asynccontextmanager
    async def _connect_streamable_http_webhook(self):
        """Connect to StreamableHTTP+Webhook backend."""
        config = self.config
        if not isinstance(config, StreamableHTTPWebhookClientConfig):
            raise TypeError(f"Expected StreamableHTTPWebhookClientConfig, got {type(config)}")

        async with streamable_http_webhook_client(config=config) as streams:
            # streamable_http_webhook_client returns a 3-tuple, we only need the first two
            read_stream, write_stream = streams[0], streams[1]
            logger.debug(f"ProxyClient[{self.session_id}]: Connected to StreamableHTTP+Webhook backend")
            yield read_stream, write_stream


def create_backend_client(
    transport_type: BackendTransportType,
    config: BackendConfig,
    low_level_clients: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> ProxyClient:
    """Create a backend client for the specified transport.

    This is a convenience function for creating ProxyClient instances.

    Args:
        transport_type: The type of backend transport
        config: Configuration for the backend transport
        low_level_clients: Low-level clients required by transport
        session_id: Optional session ID for tracking

    Returns:
        ProxyClient: Configured client instance
    """
    return ProxyClient(
        transport_type=transport_type,
        config=config,
        low_level_clients=low_level_clients,
        session_id=session_id,
    )
