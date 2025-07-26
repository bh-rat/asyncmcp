"""
Webhook server transport implementation.

The server receives HTTP POST requests from clients and sends responses via webhooks.
"""

import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from anyio.streams.memory import MemoryObjectSendStream
from mcp import JSONRPCError
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from asyncmcp.common.server import ServerTransport
from asyncmcp.webhook.utils import WebhookServerConfig, send_webhook_response

logger = logging.getLogger(__name__)


class WebhookTransport(ServerTransport):
    """Webhook transport for individual client sessions."""

    def __init__(
        self,
        config: WebhookServerConfig,
        http_client: httpx.AsyncClient,
        session_id: str | None,
        webhook_url: str | None = None,
        outgoing_message_sender: MemoryObjectSendStream[OutgoingMessageEvent] | None = None,
    ):
        super().__init__(config, session_id, outgoing_message_sender)
        self.http_client = http_client
        self.webhook_url = webhook_url

    def set_webhook_url(self, webhook_url: str) -> None:
        """Set the client-specific webhook URL."""
        self.webhook_url = webhook_url

    async def send_to_client_webhook(self, session_message: SessionMessage) -> None:
        """Send a message to the client's webhook URL."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping webhook send")
            return

        if not self.webhook_url:
            logger.warning(f"No webhook URL set for session {self.session_id}")
            return

        try:
            await send_webhook_response(
                self.http_client,
                self.webhook_url,
                session_message,
                self.session_id,
                None,  # client_id not needed for webhook headers in this context
            )
            logger.debug(f"Successfully sent response to webhook {self.webhook_url}")

        except httpx.ConnectError as e:
            logger.error(f"Failed to connect to webhook {self.webhook_url}: {e}")
            # Re-raise ConnectError as it indicates a critical connection failure
            raise
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error sending message to webhook {self.webhook_url}: {e}")
            # Don't re-raise other HTTP errors to avoid breaking the session
        except Exception as e:
            logger.error(f"Unexpected error sending message to webhook {self.webhook_url}: {e}")
            # Only raise for unexpected errors

    async def send_message(self, session_message: SessionMessage) -> None:
        """Send a message to this session's read stream."""
        if self._terminated or not self._read_stream_writer:
            return

        try:
            await self._read_stream_writer.send(session_message)
        except Exception as e:
            logger.warning(f"Error sending message to session {self.session_id}: {e}")

    async def send_error_to_client_webhook(self, error_response: JSONRPCError) -> None:
        """Send an error response to the client's webhook URL."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping error send")
            return

        if not self.webhook_url:
            logger.warning(f"No webhook URL set for session {self.session_id}")
            return

        try:
            error_message = JSONRPCMessage(root=error_response)
            error_session_message = SessionMessage(error_message)
            await self.send_to_client_webhook(error_session_message)
            logger.debug(f"Sent error response to webhook: {error_response.error.message}")

        except Exception as e:
            logger.error(f"Error sending error response to webhook {self.webhook_url}: {e}")
            # Don't re-raise here to avoid error loops


@asynccontextmanager
async def webhook_server(config: WebhookServerConfig, http_client: httpx.AsyncClient, webhook_url: str):
    """Easy wrapper for initiating a webhook server transport"""
    session_id = str(uuid.uuid4())
    transport = WebhookTransport(config, http_client, session_id, webhook_url=webhook_url)

    async with transport.connect() as (read_stream, write_stream):
        yield read_stream, write_stream
