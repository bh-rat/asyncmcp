"""
SNS/SQS server transport implementation.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio.to_thread
from anyio.streams.memory import MemoryObjectSendStream
from mcp import JSONRPCError
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from asyncmcp.common.aws_queue_utils import create_common_server_message_attributes
from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from asyncmcp.common.server import ServerTransport
from asyncmcp.sns_sqs.utils import SnsSqsServerConfig

logger = logging.getLogger(__name__)


class SnsSqsTransport(ServerTransport):
    """SNS/SQS transport for individual client sessions."""

    def __init__(
        self,
        config: SnsSqsServerConfig,
        sqs_client: Any,
        sns_client: Any,
        session_id: Optional[str],
        client_topic_arn: Optional[str] = None,
        outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None,
    ):
        super().__init__(config, session_id, outgoing_message_sender)
        self.sqs_client = sqs_client
        self.sns_client = sns_client
        self.client_topic_arn = client_topic_arn

    def set_client_topic_arn(self, topic_arn: str):
        """Set the client-specific topic arn."""
        self.client_topic_arn = topic_arn

    @staticmethod
    async def _create_sns_message_attributes(
        session_message: SessionMessage,
        config: SnsSqsServerConfig,
        session_id: Optional[str],
        protocol_version: Optional[str] = None,
    ) -> dict:
        """Create SNS message attributes for server-side messages."""
        attrs = create_common_server_message_attributes(
            session_message=session_message, session_id=session_id, protocol_version=protocol_version
        )

        if config.message_attributes:
            for key, value in config.message_attributes.items():
                attrs[key] = {"DataType": "String", "StringValue": str(value)}

        return attrs

    async def send_to_client_topic(self, session_message: SessionMessage) -> None:
        """Write messages to SNS."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping SNS send")
            return

        if not self.client_topic_arn:
            logger.warning(f"No response topic arn set for session {self.session_id}")
            return

        try:
            json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
            message_attributes = await self._create_sns_message_attributes(
                session_message, self.config, self.session_id
            )

            await anyio.to_thread.run_sync(
                lambda: self.sns_client.publish(
                    TopicArn=self.client_topic_arn, Message=json_message, MessageAttributes=message_attributes
                )
            )
        except Exception as e:
            logger.error(f"Error in sending message to topic {self.client_topic_arn}: {e}")
            raise

    async def send_error_to_client_topic(self, error_response: JSONRPCError) -> None:
        """Send an error response to the client's topic."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping error send")
            return

        if not self.client_topic_arn:
            logger.warning(f"No response topic arn set for session {self.session_id}")
            return

        try:
            # Create a SessionMessage from the error response
            error_message = JSONRPCMessage(root=error_response)
            error_session_message = SessionMessage(error_message)
            await self.send_to_client_topic(error_session_message)
            logger.debug(f"Sent error response to client topic: {error_response.error.message}")

        except Exception as e:
            logger.error(f"Error sending error response to client topic {self.client_topic_arn}: {e}")
            # Don't re-raise here to avoid error loops


@asynccontextmanager
async def sns_sqs_server(config: SnsSqsServerConfig, sqs_client: Any, sns_client: Any, client_topic_arn: str):
    """Easy wrapper for initiating a SQS server transport"""
    transport = SnsSqsTransport(config, sqs_client, sns_client, client_topic_arn)

    async with transport.connect() as (read_stream, write_stream):
        yield read_stream, write_stream
