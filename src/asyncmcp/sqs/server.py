# src/asyncmcp/sqs/transport.py
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio
import anyio.to_thread
from anyio.streams.memory import MemoryObjectSendStream
from mcp import JSONRPCError
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from asyncmcp.common.aws_queue_utils import create_common_server_message_attributes
from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from asyncmcp.common.server import ServerTransport
from asyncmcp.sqs.utils import SqsServerConfig

logger = logging.getLogger(__name__)


class SqsTransport(ServerTransport):
    """
    SQS transport for handling a single MCP session.
    """

    def __init__(
        self,
        config: SqsServerConfig,
        sqs_client: Any,
        session_id: Optional[str] = None,
        response_queue_url: Optional[str] = None,
        outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None,
    ):
        super().__init__(config, session_id, outgoing_message_sender)
        self.sqs_client = sqs_client
        self.response_queue_url = response_queue_url

    def set_response_queue_url(self, response_queue_url: str) -> None:
        """Set the client-specific response queue URL."""
        self.response_queue_url = response_queue_url

    async def _create_sqs_message_attributes(
        self, session_message: SessionMessage, protocol_version: Optional[str] = None
    ) -> dict:
        """Create SQS message attributes for outgoing messages."""
        attrs = create_common_server_message_attributes(
            session_message=session_message, session_id=self.session_id, protocol_version=protocol_version
        )

        if self.config.message_attributes:
            for key, value in self.config.message_attributes.items():
                attrs[key] = {"DataType": "String", "StringValue": str(value)}

        return attrs

    async def send_to_client_queue(self, session_message: SessionMessage) -> None:
        """Send a message to the client's response queue."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping SQS send")
            return

        if not self.response_queue_url:
            logger.warning(f"No response queue URL set for session {self.session_id}")
            return

        try:
            json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

            message_attributes = await self._create_sqs_message_attributes(session_message)

            await anyio.to_thread.run_sync(
                lambda: self.sqs_client.send_message(
                    QueueUrl=self.response_queue_url,
                    MessageBody=json_message,
                    MessageAttributes=message_attributes,
                )
            )

        except Exception as e:
            logger.error(f"Error sending message to client queue {self.response_queue_url}: {e}")
            raise

    async def send_error_to_client_queue(self, error_response: JSONRPCError) -> None:
        """Send an error response to the client's response queue."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping error send")
            return

        if not self.response_queue_url:
            logger.warning(f"No response queue URL set for session {self.session_id}")
            return

        try:
            # Create a SessionMessage from the error response
            error_message = JSONRPCMessage(root=error_response)
            error_session_message = SessionMessage(error_message)
            await self.send_to_client_queue(error_session_message)
            logger.debug(f"Sent error response to client queue: {error_response.error.message}")

        except Exception as e:
            logger.error(f"Error sending error response to client queue {self.response_queue_url}: {e}")
            # Don't re-raise here to avoid error loops


@asynccontextmanager
async def sqs_server(config: SqsServerConfig, sqs_client: Any, response_queue_url: str):
    """Easy wrapper for initiating a SQS server transport"""
    transport = SqsTransport(config, sqs_client, response_queue_url=response_queue_url)

    async with transport.connect() as (read_stream, write_stream):
        yield read_stream, write_stream
