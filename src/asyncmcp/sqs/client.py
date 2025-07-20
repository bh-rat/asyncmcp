import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from collections.abc import AsyncGenerator
import json

import mcp.types as types
from mcp.shared.message import SessionMessage

from .utils import SqsTransportConfig, process_sqs_message

logger = logging.getLogger(__name__)


async def _create_sqs_message_attributes(
        session_message: SessionMessage, config: SqsTransportConfig, client_id: str, session_id: str | None
) -> Dict[str, Any]:
    attrs = {
        "MessageType": {"DataType": "String", "StringValue": "jsonrpc"},
        "ClientId": {"DataType": "String", "StringValue": client_id},
        "Timestamp": {"DataType": "Number", "StringValue": str(int(time.time()))},
    }

    message_root = session_message.message.root
    if isinstance(message_root, types.JSONRPCRequest):
        attrs.update(
            {
                "RequestId": {"DataType": "String", "StringValue": str(message_root.id)},
                "Method": {"DataType": "String", "StringValue": message_root.method},
            }
        )
    elif isinstance(message_root, types.JSONRPCNotification):
        attrs["Method"] = {"DataType": "String", "StringValue": message_root.method}

    if config.message_attributes:
        attrs.update(config.message_attributes)

    if session_id:
        attrs["SessionId"] = {"DataType": "String", "StringValue": session_id}

    return attrs


@asynccontextmanager
async def sqs_client(
    config: SqsTransportConfig, sqs_client: Any, response_queue_url: str
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]],
    None,
]:
    client_id = config.client_id or str(uuid.uuid4())
    session_id = None

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def sqs_reader() -> None:
        # TODO : move away from nonlocal session_id to avoid race conditions and coupling
        nonlocal session_id
        async with read_stream_writer:
            while True:
                await anyio.lowlevel.checkpoint()
                try:
                    response = await anyio.to_thread.run_sync(
                        lambda: sqs_client.receive_message(
                            QueueUrl=response_queue_url,
                            MaxNumberOfMessages=config.max_messages,
                            WaitTimeSeconds=config.wait_time_seconds,
                            VisibilityTimeout=config.visibility_timeout_seconds,
                            MessageAttributeNames=["All"],
                        )
                    )
                    messages = response.get("Messages", [])
                    if messages:
                        for message in messages:
                            if session_id is None:
                                msg_attrs = message.get("MessageAttributes", {})
                                if "SessionId" in msg_attrs:
                                    session_id = msg_attrs["SessionId"]["StringValue"]

                        await process_sqs_message(messages, sqs_client, response_queue_url, read_stream_writer)
                    else:
                        await anyio.sleep(config.poll_interval_seconds)
                except Exception as e:
                    logger.warning(f"Error receiving messages from SQS: {e}")
                    await anyio.sleep(min(config.poll_interval_seconds, 1.0))

    async def sqs_writer() -> None:
        nonlocal session_id
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                await anyio.lowlevel.checkpoint()
                try:
                    json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    message_attributes = await _create_sqs_message_attributes(session_message, config, client_id,
                                                                              session_id)
                    # For initialize requests, add response_queue_url to the params
                    if (
                        isinstance(session_message.message.root, types.JSONRPCRequest)
                        and session_message.message.root.method == "initialize"
                    ):
                        message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
                        if "params" not in message_dict:
                            message_dict["params"] = {}
                        message_dict["params"]["response_queue_url"] = response_queue_url
                        json_message = json.dumps(message_dict)

                    await anyio.to_thread.run_sync(
                        lambda: sqs_client.send_message(
                            QueueUrl=config.read_queue_url,
                            MessageBody=json_message,
                            MessageAttributes=message_attributes,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Error sending message to SQS: {e}")
                    # Continue processing other messages even if one fails

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
