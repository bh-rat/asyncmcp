# src/asyncmcp/sqs/transport.py
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio
import anyio.to_thread
from anyio.streams.memory import MemoryObjectSendStream
from mcp.shared.message import SessionMessage

from asyncmcp.common.aws_queue_utils import create_common_client_message_attributes
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

    async def _create_sqs_message_attributes(self, session_message: SessionMessage) -> dict:
        """Create SQS message attributes for outgoing messages."""
        attrs = create_common_client_message_attributes(session_message, session_id=self.session_id, client_id=None)

        if self.config.message_attributes:
            for key, value in self.config.message_attributes.items():
                attrs[key] = {"DataType": "String", "StringValue": str(value)}

        return attrs

    async def send_to_client_queue(self, session_message: SessionMessage) -> None:
        """Send a message to the client's response queue."""
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


@asynccontextmanager
async def sqs_server(config: SqsServerConfig, sqs_client: Any, response_queue_url: str):
    """Easy wrapper for initiating a SQS server transport"""
    transport = SqsTransport(config, sqs_client, response_queue_url=response_queue_url)

    async with transport.connect() as (read_stream, write_stream):
        yield read_stream, write_stream
