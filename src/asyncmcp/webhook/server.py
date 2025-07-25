"""
Webhook server transport implementation.

The server receives HTTP POST requests from clients and sends responses via webhooks.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from anyio.streams.memory import MemoryObjectSendStream

from mcp.shared.message import SessionMessage

from asyncmcp.common.server import ServerTransport
from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from .utils import (
    WebhookTransportConfig,
    send_webhook_response,
)

logger = logging.getLogger(__name__)


class WebhookTransport(ServerTransport):
    """Webhook transport for individual client sessions."""

    def __init__(
        self,
        config: WebhookTransportConfig,
        http_client: httpx.AsyncClient,
        session_id: Optional[str],
        webhook_url: Optional[str] = None,
        outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None,
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
            # Add logging to track what's being sent to the MCP server
            message_root = session_message.message.root
            logger.info(
                f"[WebhookTransport.send_message] Sending to MCP server: method={getattr(message_root, 'method', 'N/A')} id={getattr(message_root, 'id', 'N/A')} session_id={self.session_id}"
            )

            # Log the full message for debugging
            json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
            logger.info(f"[WebhookTransport.send_message] Full message: {json_message}")

            await self._read_stream_writer.send(session_message)
            logger.info(f"[WebhookTransport.send_message] Successfully sent message to MCP server")
        except Exception as e:
            logger.warning(f"Error sending message to session {self.session_id}: {e}")


@asynccontextmanager
async def webhook_server(config: WebhookTransportConfig, http_client: httpx.AsyncClient, webhook_url: str):
    """Easy wrapper for initiating a webhook server transport"""
    import uuid

    session_id = str(uuid.uuid4())
    transport = WebhookTransport(config, http_client, session_id, webhook_url=webhook_url)

    async with transport.connect() as (read_stream, write_stream):
        yield read_stream, write_stream
