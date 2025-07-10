from dataclasses import dataclass
from typing import Any, Dict, Optional

import anyio
import anyio.lowlevel
import orjson
from anyio.streams.memory import MemoryObjectSendStream
from pydantic_core import ValidationError

import mcp.types as types
from mcp.shared.message import SessionMessage


@dataclass
class SnsSqsTransportConfig:
    sqs_queue_url: str
    sns_topic_arn: str
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    message_attributes: Optional[Dict[str, Any]] = None
    poll_interval_seconds: float = 5.0
    client_id: Optional[str] = None
    transport_timeout_seconds: Optional[float] = None


async def _to_session_message(sqs_message: Dict[str, Any]) -> SessionMessage:
    """Process a single SQS message."""
    message_body = sqs_message["Body"]

    try:
        parsed_body = orjson.loads(message_body)
        # Extract from SNS notification if needed
        actual_message = parsed_body["Message"] if "Message" in parsed_body and "Type" in parsed_body else message_body
    except orjson.JSONDecodeError:
        actual_message = message_body

    jsonrpc_message = types.JSONRPCMessage.model_validate_json(actual_message)
    return SessionMessage(jsonrpc_message)


async def _delete_sqs_message(sqs_client: Any, queue_url: str, receipt_handle: str) -> None:
    """Delete SQS message."""
    await anyio.to_thread.run_sync(lambda: sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle))


async def process_sqs_message(
    messages: list[Dict[str, Any]],
    sqs_client: Any,
    config: SnsSqsTransportConfig,
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception],
) -> None:
    async def process_single_message(sqs_message: Dict[str, Any]) -> None:
        try:
            session_message = await _to_session_message(sqs_message)
            await read_stream_writer.send(session_message)
            await _delete_sqs_message(sqs_client, config.sqs_queue_url, sqs_message["ReceiptHandle"])

        except (ValidationError, orjson.JSONDecodeError) as exc:
            await read_stream_writer.send(exc)
            # Delete invalid messages to prevent reprocessing
            await _delete_sqs_message(sqs_client, config.sqs_queue_url, sqs_message["ReceiptHandle"])

        except Exception as exc:
            await read_stream_writer.send(exc)

    await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        for msg in messages:
            tg.start_soon(process_single_message, msg)
