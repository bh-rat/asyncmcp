import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Dict

import anyio
import anyio.lowlevel
import anyio.to_thread
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage

from asyncmcp.common.aws_queue_utils import create_common_client_message_attributes
from asyncmcp.common.aws_queue_utils import sqs_reader as common_sqs_reader
from asyncmcp.common.base_client import BaseClientTransport
from asyncmcp.common.client_state import ClientState
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig

logger = logging.getLogger(__name__)


class SnsSqsClientTransport(BaseClientTransport):
    """SNS+SQS-specific client transport with MCP protocol compliance."""

    def __init__(self, config: SnsSqsClientConfig, sqs_client: Any, sns_client: Any, client_topic_arn: str):
        client_id = config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}"
        state = ClientState(client_id=client_id, session_id=None)
        super().__init__(state)
        self.config = config
        self.sqs_client = sqs_client
        self.sns_client = sns_client
        self.client_topic_arn = client_topic_arn

    async def _create_sns_message_attributes(self, session_message: SessionMessage) -> Dict[str, Any]:
        """Create SNS message attributes with protocol version support."""
        attrs = create_common_client_message_attributes(
            session_message=session_message,
            client_id=self.client_state.client_id,
            session_id=self.client_state.session_id,
            protocol_version=self.protocol_version or None,
        )

        # Add config-specific attributes
        if self.config.message_attributes:
            for key, value in self.config.message_attributes.items():
                attrs[key] = {"DataType": "String", "StringValue": str(value)}

        return attrs

    async def _prepare_initialize_request(self, session_message: SessionMessage) -> str:
        """Prepare initialize request with client_topic_arn in params."""
        if not self._is_initialization_request(session_message.message):
            return session_message.message.model_dump_json(by_alias=True, exclude_none=True)

        message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
        if "params" not in message_dict:
            message_dict["params"] = {}
        message_dict["params"]["client_topic_arn"] = self.client_topic_arn
        return json.dumps(message_dict)

    async def send_message(self, session_message: SessionMessage) -> None:
        """Send message to SNS with proper error handling."""
        try:
            json_message = await self._prepare_initialize_request(session_message)
            message_attributes = await self._create_sns_message_attributes(session_message)

            await anyio.to_thread.run_sync(
                lambda: self.sns_client.publish(
                    TopicArn=self.config.sns_topic_arn, Message=json_message, MessageAttributes=message_attributes
                )
            )
        except Exception as e:
            logger.warning(f"Error sending message to SNS: {e}")
            # Don't raise - let the client continue processing other messages


@asynccontextmanager
async def sns_sqs_client(
    config: SnsSqsClientConfig,
    sqs_client: Any,
    sns_client: Any,
    client_topic_arn: str,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]], None
]:
    transport = SnsSqsClientTransport(config, sqs_client, sns_client, client_topic_arn)

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def sqs_reader():
        await common_sqs_reader(read_stream_writer, sqs_client, config, config.sqs_queue_url, transport.client_state)

    async def sns_writer():
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                await anyio.lowlevel.checkpoint()
                await transport.send_message(session_message)

    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(sqs_reader)
            tg.start_soon(sns_writer)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(sqs_reader)
                tg.start_soon(sns_writer)
                try:
                    yield read_stream, write_stream
                finally:
                    tg.cancel_scope.cancel()
