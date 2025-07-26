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
from mcp.types import JSONRPCMessage

from asyncmcp.common.aws_queue_utils import create_common_client_message_attributes
from asyncmcp.common.aws_queue_utils import sqs_reader as common_sqs_reader
from asyncmcp.common.base_client import BaseClientTransport
from asyncmcp.common.client_state import ClientState
from asyncmcp.sqs.utils import SqsClientConfig

logger = logging.getLogger(__name__)


class SqsClientTransport(BaseClientTransport):
    """SQS-specific client transport with MCP protocol compliance."""

    def __init__(self, config: SqsClientConfig, sqs_client: Any):
        client_id = config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}"
        state = ClientState(client_id=client_id, session_id=None)
        super().__init__(state)
        self.config = config
        self.sqs_client = sqs_client

    async def _create_sqs_message_attributes(self, session_message: SessionMessage) -> Dict[str, Any]:
        """Creates SQS message attributes with protocol version support."""
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
        """Prepare initialize request with response_queue_url in params."""
        if not self._is_initialization_request(session_message.message):
            return session_message.message.model_dump_json(by_alias=True, exclude_none=True)

        message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
        if "params" not in message_dict:
            message_dict["params"] = {}
        message_dict["params"]["response_queue_url"] = self.config.response_queue_url
        return json.dumps(message_dict)

    async def send_message(self, session_message: SessionMessage) -> None:
        """Send message to SQS with proper error handling."""
        try:
            json_message = await self._prepare_initialize_request(session_message)
            message_attributes = await self._create_sqs_message_attributes(session_message)

            await anyio.to_thread.run_sync(
                lambda: self.sqs_client.send_message(
                    QueueUrl=self.config.read_queue_url,
                    MessageBody=json_message,
                    MessageAttributes=message_attributes,
                )
            )
        except Exception as e:
            logger.warning(f"Error sending message to SQS: {e}")
            # Don't raise - let the client continue processing other messages

    async def handle_received_message(self, session_message: SessionMessage, sqs_message_attrs: Dict[str, Any]) -> None:
        """Handle received message with session and protocol management."""
        # Extract session ID from SQS message attributes for initialize responses only
        session_id_source = None
        if "SessionId" in sqs_message_attrs:
            session_id_source = sqs_message_attrs["SessionId"]["StringValue"]

        # Let base class handle protocol version and session management
        if not isinstance(session_message.message, JSONRPCMessage):
            raise TypeError("session_message.message must be of type JSON-RPC message")

        await super().handle_received_message(session_message.message, session_id_source)


@asynccontextmanager
async def sqs_client(
    config: SqsClientConfig, sqs_client: Any
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]],
    None,
]:
    transport = SqsClientTransport(config, sqs_client)

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def sqs_reader():
        await common_sqs_reader(
            read_stream_writer, sqs_client, config, config.response_queue_url, transport.client_state
        )

    async def sqs_writer() -> None:
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                await anyio.lowlevel.checkpoint()
                await transport.send_message(session_message)

    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(sqs_reader)
            tg.start_soon(sqs_writer)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(sqs_reader)
                tg.start_soon(sqs_writer)
                yield read_stream, write_stream
