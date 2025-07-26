# src/asyncmcp/sns_sqs/manager.py
import json
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from uuid import uuid4

import anyio
import anyio.lowlevel
import anyio.to_thread
import mcp.types as types
from anyio.abc import TaskStatus
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.shared.message import SessionMessage

from asyncmcp.common.aws_queue_utils import (
    delete_sqs_message,
)
from asyncmcp.common.utils import (
    create_internal_error_response,
    create_session_id_error_response,
    create_session_not_found_error_response,
    create_session_terminated_error_response,
    is_initialize_request,
    validate_and_parse_message,
    validate_message_attributes,
    validate_session_id,
)
from asyncmcp.sns_sqs.server import OutgoingMessageEvent, SnsSqsTransport
from asyncmcp.sns_sqs.utils import SnsSqsServerConfig

logger = logging.getLogger(__name__)


class SnsSqsSessionManager:
    def __init__(
        self, app: MCPServer, config: SnsSqsServerConfig, sqs_client: Any, sns_client: Any, stateless: bool = False
    ):
        self.app = app
        self.config = config
        self.sqs_client = sqs_client
        self.sns_client = sns_client
        self.stateless = stateless

        self._session_lock = anyio.Lock()
        self._transport_instances: Dict[str, SnsSqsTransport] = {}

        self._outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None
        self._outgoing_message_receiver: Optional[MemoryObjectReceiveStream[OutgoingMessageEvent]] = None

        self._task_group: Optional[anyio.abc.TaskGroup] = None
        self._run_lock = threading.Lock()
        self._has_started = False

    @asynccontextmanager
    async def run(self):
        """
        Run the session manager with SNS/SQS message processing.

        Starts the main SQS listener that handles initialize requests
        and routes messages to appropriate sessions.
        """
        with self._run_lock:
            if self._has_started:
                raise RuntimeError("SnsSqsSessionManager.run() can only be called once per instance.")
            self._has_started = True

        self._outgoing_message_sender, self._outgoing_message_receiver = anyio.create_memory_object_stream[
            OutgoingMessageEvent
        ](1000)

        async with anyio.create_task_group() as tg:
            self._task_group = tg
            tg.start_soon(self._sqs_message_processor)
            tg.start_soon(self._event_driven_message_sender)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()
                await self._shutdown_all_sessions()

                if self._outgoing_message_sender:
                    await self._outgoing_message_sender.aclose()
                if self._outgoing_message_receiver:
                    await self._outgoing_message_receiver.aclose()

                self._task_group = None

    async def _sqs_message_processor(self) -> None:
        """
        Main SQS message processing loop.

        Listens for messages and either:
        1. Creates new session for 'initialize' requests
        2. Routes to existing session based on SessionId
        """
        try:
            while True:
                # Check for cancellation
                await anyio.lowlevel.checkpoint()

                try:
                    # Poll SQS for messages
                    response = await anyio.to_thread.run_sync(
                        lambda: self.sqs_client.receive_message(
                            QueueUrl=self.config.sqs_queue_url,
                            MaxNumberOfMessages=self.config.max_messages,
                            WaitTimeSeconds=self.config.wait_time_seconds,
                            VisibilityTimeout=self.config.visibility_timeout_seconds,
                            MessageAttributeNames=["All"],
                        )
                    )

                    messages = response.get("Messages", [])
                    if messages:
                        # Process each message
                        for sqs_message in messages:
                            await self._process_single_message(sqs_message)
                    else:
                        await anyio.sleep(self.config.poll_interval_seconds)

                except Exception as e:
                    logger.error(f"Error in SNS/SQS message processor: {e}")
                    await anyio.sleep(min(self.config.poll_interval_seconds, 1.0))
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as e:
            logger.error(f"Fatal error in SNS/SQS message processor: {e}")
            raise

    async def _process_single_message(self, sqs_message: Dict[str, Any]) -> None:
        receipt_handle = sqs_message["ReceiptHandle"]

        try:
            combined_attrs = await self._extract_combined_message_attributes(sqs_message)

            error_response = validate_message_attributes(combined_attrs)
            if error_response:
                logger.warning(f"Message validation failed: {error_response.error.message}")
                await self._send_error_response_if_possible(error_response, combined_attrs)
                await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, receipt_handle)
                return

            # Parse and validate message body
            body = sqs_message["Body"]
            actual_message = await self._extract_message_body(body)

            session_message, parse_error = validate_and_parse_message(actual_message)
            if parse_error:
                logger.warning(f"Message parsing failed: {parse_error.error.message}")
                await self._send_error_response_if_possible(parse_error, combined_attrs)
                await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, receipt_handle)
                return

            session_id = combined_attrs.get("SessionId", {}).get("StringValue")
            protocol_version = combined_attrs.get("ProtocolVersion", {}).get("StringValue")

            is_init_request = is_initialize_request(session_message)

            if is_init_request:
                await self._handle_initialize_request(session_message, session_id, sqs_message, protocol_version)
            else:
                # For non-initialize requests, session ID is required
                if not session_id:
                    error_response = create_session_id_error_response()
                    await self._send_error_response_if_possible(error_response, combined_attrs)
                    await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, receipt_handle)
                    return

                if session_id in self._transport_instances:
                    transport = self._transport_instances[session_id]
                    if transport.is_terminated:
                        error_response = create_session_terminated_error_response(session_id)
                        await self._send_error_response_if_possible(error_response, combined_attrs)
                        await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, receipt_handle)
                        return

                    await transport.send_message(session_message)
                else:
                    error_response = create_session_not_found_error_response(session_id)
                    await self._send_error_response_if_possible(error_response, combined_attrs)
                    logger.warning(f"No session found for message with SessionId: {session_id}")

            await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, receipt_handle)

        except Exception as e:
            logger.error(f"Error processing SQS message: {e}")
            error_response = create_internal_error_response(f"Internal server error: {str(e)}")
            try:
                combined_attrs = await self._extract_combined_message_attributes(sqs_message)
                await self._send_error_response_if_possible(error_response, combined_attrs)
            except:
                pass
            await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, receipt_handle)

    async def _extract_combined_message_attributes(self, sqs_message: Dict[str, Any]) -> Dict[str, Any]:
        """Extract message attributes from both SQS and SNS sources."""
        combined_attrs = {}

        # SQS message attributes
        message_attrs = sqs_message.get("MessageAttributes", {})
        for key, value in message_attrs.items():
            combined_attrs[key] = value

        # SNS notification attributes
        try:
            body = json.loads(sqs_message["Body"])
            if "MessageAttributes" in body:
                sns_attrs = body["MessageAttributes"]
                for key, value in sns_attrs.items():
                    if key not in combined_attrs:  # Don't override SQS attributes
                        # Convert SNS format to SQS format
                        combined_attrs[key] = {"StringValue": value.get("Value", "")}
        except (json.JSONDecodeError, KeyError):
            pass

        return combined_attrs

    async def _extract_message_body(self, body: Any) -> str:
        """Extract the actual message body, handling SNS notification format."""
        if isinstance(body, str):
            try:
                parsed_body = json.loads(body)
                if "Message" in parsed_body and "Type" in parsed_body:
                    # This is an SNS notification, extract the actual message
                    return parsed_body["Message"]
                else:
                    return body
            except json.JSONDecodeError:
                return body
        else:
            # If body is already a dict, convert to JSON string first
            return json.dumps(body)

    async def _send_error_response_if_possible(self, error_response, message_attrs: Dict[str, Any]) -> None:
        """Send an error response back to the client if we can determine the topic."""
        try:
            session_id = message_attrs.get("SessionId", {}).get("StringValue")
            if session_id and session_id in self._transport_instances:
                transport = self._transport_instances[session_id]
                if not transport.is_terminated and transport.client_topic_arn:
                    await transport.send_error_to_client_topic(error_response)
                    return

        except Exception as e:
            logger.debug(f"Could not send error response: {e}")

    async def _handle_initialize_request(
        self,
        session_message: SessionMessage,
        session_id: str | None,
        sqs_message: Dict[str, Any],
        protocol_version: Optional[str] = None,
    ) -> None:
        if session_id and session_id in self._transport_instances:
            transport = self._transport_instances[session_id]
            await transport.send_message(session_message)
            return

        client_topic_arn = None
        message_root = session_message.message.root
        if isinstance(message_root, types.JSONRPCRequest) and message_root.params:
            if isinstance(message_root.params, dict):
                client_topic_arn = message_root.params.get("client_topic_arn")

        if not client_topic_arn:
            logger.error("Initialize request missing required 'client_topic_arn' parameter")
            await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, sqs_message["ReceiptHandle"])
            return

        async with self._session_lock:
            if not session_id:
                session_id = uuid4().hex

            if not validate_session_id(session_id):
                logger.error(f"Generated invalid session ID: {session_id}")
                session_id = uuid4().hex

            transport = SnsSqsTransport(
                config=self.config,
                sqs_client=self.sqs_client,
                sns_client=self.sns_client,
                session_id=session_id,
                client_topic_arn=client_topic_arn,
                outgoing_message_sender=self._outgoing_message_sender,
            )

            self._transport_instances[session_id] = transport

            async def run_session(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED):
                try:
                    async with transport.connect() as (read_stream, write_stream):
                        task_status.started()
                        logger.debug(f"Started SNS+SQS session: {session_id} with protocol version: {protocol_version}")

                        await self.app.run(
                            read_stream,
                            write_stream,
                            self.app.create_initialization_options(),
                            stateless=self.stateless,
                        )
                except Exception as e:
                    logger.error(f"Session {session_id} crashed: {e}", exc_info=True)
                finally:
                    async with self._session_lock:
                        if session_id in self._transport_instances:
                            logger.debug(f"Cleaning up session: {session_id}")
                            del self._transport_instances[session_id]

            assert self._task_group is not None
            await self._task_group.start(run_session)

            await transport.send_message(session_message)

    async def _event_driven_message_sender(self) -> None:
        """
        Background task to send outgoing messages from sessions back to SNS topics.
        """
        if not self._outgoing_message_receiver:
            logger.error("No outgoing message receiver configured")
            return

        try:
            async with self._outgoing_message_receiver:
                async for message_event in self._outgoing_message_receiver:
                    try:
                        if not message_event.session_id:
                            logger.warning("Received message event with no session_id")
                            continue
                        transport = self._transport_instances.get(message_event.session_id)
                        if not transport or transport.is_terminated:
                            logger.warning(
                                f"Received message for unknown/terminated session: {message_event.session_id}"
                            )
                            continue

                        await transport.send_to_client_topic(message_event.message)

                    except Exception as e:
                        logger.error(f"Error processing outgoing message for session {message_event.session_id}: {e}")

        except anyio.get_cancelled_exc_class():
            raise
        except Exception as e:
            logger.error(f"Fatal error in SNS message sender: {e}")
            raise

    async def terminate_session(self, session_id: str) -> bool:
        async with self._session_lock:
            if session_id in self._transport_instances:
                transport = self._transport_instances[session_id]
                await transport.terminate()
                del self._transport_instances[session_id]
                return True
            return False

    def get_all_sessions(self) -> Dict[str, dict]:
        return {
            session_id: {"session_id": session_id, "terminated": transport.is_terminated}
            for session_id, transport in self._transport_instances.items()
        }

    async def _shutdown_all_sessions(self) -> None:
        sessions_to_terminate = list(self._transport_instances.keys())

        for session_id in sessions_to_terminate:
            try:
                await self.terminate_session(session_id)
            except Exception as e:
                logger.error(f"Error terminating session {session_id}: {e}")

        self._transport_instances.clear()
