from typing import Optional, Any, Dict

import anyio
import logging
import json
import time

import mcp.types as types
from anyio.streams.memory import MemoryObjectSendStream
from jsonschema import ValidationError
from mcp.shared.message import SessionMessage

from asyncmcp.common.client_state import ClientState


logger = logging.getLogger(__name__)


def create_common_client_message_attributes(
    session_message: SessionMessage,
    client_id: Optional[str],
    session_id: Optional[str],
):
    """Creates common message attributes."""
    attrs = {
        "MessageType": {"DataType": "String", "StringValue": "jsonrpc"},
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

    if session_id:
        attrs["SessionId"] = {"DataType": "String", "StringValue": session_id}

    if client_id:
        attrs["ClientId"] = {"DataType": "String", "StringValue": client_id}

    return attrs


async def to_session_message(sqs_message: Dict[str, Any]) -> SessionMessage:
    """Convert SQS message to SessionMessage."""
    try:
        body = sqs_message["Body"]

        # Handle SNS notification format
        if isinstance(body, str):
            try:
                parsed_body = json.loads(body)
                if "Message" in parsed_body and "Type" in parsed_body:
                    # This is an SNS notification, extract the actual message
                    actual_message = parsed_body["Message"]
                else:
                    actual_message = body
            except json.JSONDecodeError:
                actual_message = body
        else:
            # If body is already a dict, convert to JSON string first
            actual_message = json.dumps(body)

        # Parse the JSON-RPC message
        if isinstance(actual_message, str):
            parsed = json.loads(actual_message)
        else:
            parsed = actual_message

        message = types.JSONRPCMessage.model_validate(parsed)
        return SessionMessage(message)
    except Exception as e:
        raise ValueError(f"Invalid JSON-RPC message: {e}")


async def process_single_message(
    sqs_message: Dict[str, Any],
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception],
    sqs_client: Any,
    queue_url: str,
) -> None:
    try:
        session_message = await to_session_message(sqs_message)
        await read_stream_writer.send(session_message)
        await delete_sqs_message(sqs_client, queue_url, sqs_message["ReceiptHandle"])
    except (ValidationError, Exception) as exc:  # noqa: PERF203
        await read_stream_writer.send(exc)
        await delete_sqs_message(sqs_client, queue_url, sqs_message["ReceiptHandle"])


async def delete_sqs_message(sqs_client: Any, queue_url: str, receipt_handle: str) -> None:
    """Delete processed message from SQS queue."""
    try:
        sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    except Exception as e:
        logger.warning(f"Failed to delete SQS message: {e}")


async def sqs_reader(
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception],
    sqs_client: Any,
    config: Any,
    queue_url: str,
    state: ClientState,
) -> None:
    async with read_stream_writer:
        while True:
            await anyio.lowlevel.checkpoint()
            try:
                response = await anyio.to_thread.run_sync(
                    lambda: sqs_client.receive_message(
                        QueueUrl=queue_url,
                        MaxNumberOfMessages=config.max_messages,
                        WaitTimeSeconds=config.wait_time_seconds,
                        VisibilityTimeout=config.visibility_timeout_seconds,
                        MessageAttributeNames=["All"],
                    )
                )
                messages = response.get("Messages", [])
                if messages:
                    for message in messages:
                        # TODO : only do this if the message is an initialized notification
                        if state.session_id is None:
                            session_id = None
                            msg_attrs = message.get("MessageAttributes", {})
                            
                            if "SessionId" in msg_attrs:
                                session_id = msg_attrs["SessionId"]["StringValue"]
                            else:
                                # Check if this is an SNS notification and extract attributes from there
                                try:
                                    body = json.loads(message["Body"])
                                    if "MessageAttributes" in body:
                                        sns_attrs = body["MessageAttributes"]
                                        if "SessionId" in sns_attrs:
                                            session_id = sns_attrs["SessionId"]["Value"]
                                except (json.JSONDecodeError, KeyError):
                                    pass
                            
                            if session_id:
                                await state.set_session_id_if_none(session_id)

                    await anyio.lowlevel.checkpoint()

                    async with anyio.create_task_group() as tg:
                        for msg in messages:
                            tg.start_soon(process_single_message, msg, read_stream_writer, sqs_client, queue_url)
                else:
                    await anyio.sleep(config.poll_interval_seconds)
            except Exception as e:
                logger.warning(f"Error receiving messages from SQS: {e}")
                import traceback

                logger.warning(traceback.format_exc())
                await anyio.sleep(min(config.poll_interval_seconds, 1.0))
