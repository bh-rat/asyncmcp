import anyio
from contextlib import asynccontextmanager
from typing import Any, Dict

from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from mcp.shared.message import SessionMessage

from asyncmcp.sqs.utils import SqsTransportConfig, process_sqs_message


async def _create_sqs_message_attributes(session_message: SessionMessage, config: SqsTransportConfig) -> Dict[str, Any]:
    attrs = {"MessageType": {"DataType": "String", "StringValue": "jsonrpc"}}

    if config.message_attributes:
        attrs.update(config.message_attributes)

    if hasattr(session_message, "metadata") and session_message.metadata:
        if isinstance(session_message.metadata, dict) and "related_request_id" in session_message.metadata:
            attrs["OriginalSQSMessageId"] = {
                "DataType": "String",
                "StringValue": session_message.metadata["related_request_id"],
            }

    return attrs


@asynccontextmanager
async def sqs_server(config: SqsTransportConfig, sqs_client: Any):
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def sqs_reader() -> None:
        async with read_stream_writer:
            while True:
                response = await anyio.to_thread.run_sync(
                    lambda: sqs_client.receive_message(
                        QueueUrl=config.read_queue_url,
                        MaxNumberOfMessages=config.max_messages,
                        WaitTimeSeconds=config.wait_time_seconds,
                        VisibilityTimeout=config.visibility_timeout_seconds,
                        MessageAttributeNames=["All"],
                    )
                )
                messages = response.get("Messages", [])
                if messages:
                    await process_sqs_message(messages, sqs_client, config.read_queue_url, read_stream_writer)
                else:
                    await anyio.sleep(config.poll_interval_seconds)

    async def sqs_writer() -> None:
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                try:
                    json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    message_attributes = await _create_sqs_message_attributes(session_message, config)
                    await anyio.to_thread.run_sync(
                        lambda: sqs_client.send_message(
                            QueueUrl=config.write_queue_url,
                            MessageBody=json_message,
                            MessageAttributes=message_attributes,
                        )
                    )
                except Exception:
                    continue

    async with anyio.create_task_group() as tg:
        tg.start_soon(sqs_reader)
        tg.start_soon(sqs_writer)
        yield read_stream, write_stream
