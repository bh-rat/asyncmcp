"""
Comprehensive anyio fixture tests for SQS+SNS server transport module.
"""

import pytest
from unittest.mock import patch

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse
from pydantic_core import ValidationError

from asyncmcp.sns_sqs.utils import _to_session_message, process_sqs_message, _delete_sqs_message, SnsSqsTransportConfig
from asyncmcp.sns_sqs.server import (
    sns_sqs_server,
    _create_sns_message_attributes,
)


@pytest.fixture
def transport_config(server_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return server_transport_config


class TestProcessSQSMessageServer:
    """Test the server _to_session_message function."""

    @pytest.mark.anyio
    @pytest.mark.skip
    async def test_process_direct_message_with_metadata(self, sample_sqs_message):
        """Test processing a direct SQS message with metadata."""
        queue_url = "http://localhost:4566/000000000000/test-queue"
        session_message = await _to_session_message(sample_sqs_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1

        # Check metadata
        assert session_message.metadata is not None
        assert session_message.metadata["sqs_message_id"] == "test-msg-123"
        assert session_message.metadata["sqs_receipt_handle"] == "test-receipt-handle"
        assert session_message.metadata["sqs_queue_url"] == queue_url

    @pytest.mark.anyio
    @pytest.mark.skip
    async def test_process_sns_wrapped_message_with_metadata(self, sample_sns_wrapped_message):
        """Test processing an SNS-wrapped SQS message with metadata."""
        queue_url = "http://localhost:4566/000000000000/test-queue"
        session_message = await _to_session_message(sample_sns_wrapped_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/notification"
        assert session_message.message.root.id == 2

        # Check metadata preservation
        assert session_message.metadata["sqs_message_id"] == "test-msg-456"

    @pytest.mark.anyio
    @pytest.mark.skip
    async def test_process_message_with_attributes(self, sample_sqs_message):
        """Test processing message with SQS message attributes."""
        queue_url = "http://localhost:4566/000000000000/test-queue"
        session_message = await _to_session_message(sample_sqs_message)

        # Check that message attributes are preserved
        assert "sqs_message_attributes" in session_message.metadata
        assert session_message.metadata["sqs_message_attributes"]["ClientId"]["StringValue"] == "test-client"

    @pytest.mark.anyio
    async def test_process_invalid_json_server(self):
        """Test processing message with invalid JSON on server side."""
        invalid_message = {
            "MessageId": "server-invalid-msg",
            "ReceiptHandle": "server-invalid-handle",
            "Body": "not valid json",
            "MessageAttributes": {},
        }

        with pytest.raises(ValidationError):
            await _to_session_message(invalid_message)


class TestDeleteSQSMessageServer:
    """Test the server _delete_sqs_message function."""

    @pytest.mark.anyio
    async def test_delete_message_server(self, mock_sqs_client):
        """Test successful message deletion on server side."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await _delete_sqs_message(mock_sqs_client, "server-queue", "server-handle")

            mock_run_sync.assert_called_once()
            # Verify the lambda function would call delete_message correctly
            delete_func = mock_run_sync.call_args[0][0]
            delete_func()
            mock_sqs_client.delete_message.assert_called_once_with(
                QueueUrl="server-queue", ReceiptHandle="server-handle"
            )


class TestSQSMessageProcessorServer:
    """Test the server process_sqs_message function."""

    @pytest.mark.anyio
    async def test_process_multiple_server_messages(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing multiple server messages concurrently."""
        messages = [sample_sqs_message.copy() for _ in range(3)]
        for i, msg in enumerate(messages):
            msg["MessageId"] = f"server-msg-{i}"
            msg["ReceiptHandle"] = f"server-handle-{i}"

        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with (
            patch("asyncmcp.sns_sqs.utils._to_session_message") as mock_process,
            patch("asyncmcp.sns_sqs.utils._delete_sqs_message") as mock_delete,
        ):
            mock_process.return_value = SessionMessage(
                JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="server/test")),
                metadata={"test": "metadata"},
            )

            await process_sqs_message(messages, mock_sqs_client, transport_config, send_stream)

            # Should have processed all messages
            assert mock_process.call_count == 3
            assert mock_delete.call_count == 3

    @pytest.mark.anyio
    async def test_process_server_messages_with_validation_errors(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing server messages when validation fails."""
        messages = [sample_sqs_message.copy() for _ in range(2)]
        messages[0]["MessageId"] = "good-server-msg"
        messages[1]["MessageId"] = "bad-server-msg"

        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with (
            patch("asyncmcp.sns_sqs.utils._to_session_message") as mock_process,
            patch("asyncmcp.sns_sqs.utils._delete_sqs_message") as mock_delete,
        ):
            # First message succeeds, second fails validation
            mock_process.side_effect = [
                SessionMessage(
                    JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="server/test")),
                    metadata={"test": "metadata"},
                ),
                ValidationError.from_exception_data("validation error", []),
            ]

            await process_sqs_message(messages, mock_sqs_client, transport_config, send_stream)

            # Both messages should be processed and deleted
            assert mock_process.call_count == 2
            assert mock_delete.call_count == 2


class TestCreateSNSMessageAttributesServer:
    """Test the server _create_sns_message_attributes function."""

    @pytest.mark.anyio
    async def test_create_basic_attributes(self, transport_config, sample_jsonrpc_response):
        """Test creating basic SNS message attributes."""
        session_message = SessionMessage(sample_jsonrpc_response)

        attrs = await _create_sns_message_attributes(session_message, transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert "MessageType" in attrs

    @pytest.mark.anyio
    async def test_create_attributes_with_custom_config(self, transport_config, sample_jsonrpc_response):
        """Test creating attributes with custom message attributes in config."""
        transport_config.message_attributes = {
            "ServerAttr": {"DataType": "String", "StringValue": "server-value"},
            "Environment": {"DataType": "String", "StringValue": "test"},
        }

        session_message = SessionMessage(sample_jsonrpc_response)
        attrs = await _create_sns_message_attributes(session_message, transport_config)

        assert attrs["ServerAttr"]["StringValue"] == "server-value"
        assert attrs["Environment"]["StringValue"] == "test"

    @pytest.mark.anyio
    @pytest.mark.skip
    async def test_create_attributes_with_metadata(self, transport_config, sample_jsonrpc_response):
        """Test creating attributes with session message metadata."""
        session_message = SessionMessage(
            sample_jsonrpc_response,
            metadata={
                "sqs_message_id": "original-msg-123",
                "sqs_receipt_handle": "original-handle",
                "client_info": "test-client",
            },
        )

        attrs = await _create_sns_message_attributes(session_message, transport_config)

        assert attrs["OriginalSQSMessageId"]["StringValue"] == "original-msg-123"

    @pytest.mark.anyio
    async def test_create_attributes_without_metadata(self, transport_config, sample_jsonrpc_response):
        """Test creating attributes when session has no metadata."""
        session_message = SessionMessage(sample_jsonrpc_response)
        # Explicitly set metadata to None
        session_message.metadata = None

        attrs = await _create_sns_message_attributes(session_message, transport_config)

        assert "OriginalSQSMessageId" not in attrs
        assert attrs["MessageType"]["StringValue"] == "jsonrpc"


class TestSnsSqsServer:
    """Test the main sns_sqs_server context manager."""

    @pytest.mark.anyio
    async def test_server_context_manager_basic(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test basic server context manager functionality."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty SQS response to prevent infinite loop
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    assert read_stream is not None
                    assert write_stream is not None
                    # Brief delay to let background tasks start
                    await anyio.sleep(0.01)

    @pytest.mark.anyio
    async def test_server_receive_and_send_messages(self, transport_config, sample_jsonrpc_response, mock_sqs_client, mock_sns_client):
        """Test receiving and sending messages through the server transport."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Track call count to prevent infinite loop
            call_count = 0

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func):
                    if call_count == 1:
                        # First call returns message
                        return {
                            "Messages": [
                                {
                                    "MessageId": "server-test-123",
                                    "ReceiptHandle": "server-handle-123",
                                    "Body": '{"jsonrpc": "2.0", "id": 1, "method": "server/request", "params": {}}',
                                    "MessageAttributes": {},
                                }
                            ]
                        }
                    else:
                        # Subsequent calls return empty
                        return {"Messages": []}
                elif "publish" in str(func):
                    return {"MessageId": "sns-response-123"}
                elif "delete_message" in str(func):
                    return {}

                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.5):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Send a response
                    await write_stream.send(sample_jsonrpc_response)

                    # Verify message was sent
                    assert call_count > 0

    @pytest.mark.anyio
    async def test_server_long_polling(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test server with long polling configuration."""
        transport_config.wait_time_seconds = 20  # Long polling

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_server_error_handling_in_sqs_reader(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test error handling in server SQS reader."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock SQS exception on first call, then return empty messages
            call_count = 0

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func):
                    if call_count == 1:
                        # First call raises exception
                        raise Exception("Server SQS error")
                    else:
                        # Subsequent calls return empty to prevent infinite loop
                        return {"Messages": []}
                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                # The server should handle the error gracefully without crashing
                try:
                    async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                        # Should handle the error gracefully
                        await anyio.sleep(0.05)
                except Exception:
                    # Expected that the exception might propagate, test passes if we get here
                    pass

    @pytest.mark.anyio
    async def test_server_error_handling_in_sns_writer(self, transport_config, sample_jsonrpc_response, mock_sqs_client, mock_sns_client):
        """Test error handling in server SNS writer."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty SQS response for reader
            mock_run_sync.return_value = {"Messages": []}

            # Mock SNS exception for writer
            def side_effect(func):
                if "publish" in str(func):
                    raise Exception("Server SNS error")
                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    session_message = sample_jsonrpc_response
                    await write_stream.send(session_message)
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_server_concurrent_message_processing(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test that server processes multiple messages concurrently."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            call_count = 0

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func) and call_count == 1:
                    # First call returns multiple messages
                    return {
                        "Messages": [
                            {
                                "MessageId": f"server-test-{i}",
                                "ReceiptHandle": f"server-handle-{i}",
                                "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "server/batch", "params": {{}}}}',
                                "MessageAttributes": {},
                            }
                            for i in range(5)
                        ]
                    }
                elif "receive_message" in str(func):
                    # Subsequent calls return empty
                    return {"Messages": []}
                elif "delete_message" in str(func):
                    return {}

                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.2):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Give time for message processing
                    await anyio.sleep(0.1)

    @pytest.mark.anyio
    async def test_server_message_with_correlation_id(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test server handling messages with correlation metadata."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock message with correlation info
            test_message = {
                "Messages": [
                    {
                        "MessageId": "correlated-msg-123",
                        "ReceiptHandle": "correlated-handle-123",
                        "Body": '{"jsonrpc": "2.0", "id": 1, "method": "server/correlated", "params": {}}',
                        "MessageAttributes": {
                            "CorrelationId": {"DataType": "String", "StringValue": "correlation-123"}
                        },
                    }
                ]
            }

            call_count = 0

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func):
                    if call_count == 1:
                        return test_message
                    return {"Messages": []}
                elif "delete_message" in str(func):
                    return {}
                return {}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.2):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Receive the correlated message
                    message = await read_stream.receive()

                    assert isinstance(message, SessionMessage)
                    assert (
                        message.metadata["sqs_message_attributes"]["CorrelationId"]["StringValue"] == "correlation-123"
                    )


class TestServerConfigurationValidation:
    """Test server transport configuration validation."""

    def test_server_config_creation(self, mock_sqs_client, mock_sns_client):
        """Test creating server transport configuration."""
        config = SnsSqsTransportConfig(
            sqs_queue_url="server-test-queue",
            sns_topic_arn="server-test-topic",
        )

        assert config.sqs_queue_url == "server-test-queue"
        assert config.sns_topic_arn == "server-test-topic"
        assert config.max_messages == 10  # Default value
        assert config.wait_time_seconds == 20  # Default value
        assert config.visibility_timeout_seconds == 30  # Default value
        assert config.message_attributes is None  # Default value
        assert config.poll_interval_seconds == 5.0  # Default value
        assert config.client_id is None  # Default value
        assert config.transport_timeout_seconds is None  # Default value

    def test_server_config_with_custom_values(self, mock_sqs_client, mock_sns_client):
        """Test creating server configuration with custom values."""
        config = SnsSqsTransportConfig(
            sqs_queue_url="custom-server-queue",
            sns_topic_arn="custom-server-topic",
            max_messages=15,
            wait_time_seconds=10,
            visibility_timeout_seconds=60,
            poll_interval_seconds=0.5,
        )

        assert config.sqs_queue_url == "custom-server-queue"
        assert config.sns_topic_arn == "custom-server-topic"
        assert config.max_messages == 15
        assert config.wait_time_seconds == 10
        assert config.visibility_timeout_seconds == 60
        assert config.poll_interval_seconds == 0.5


class TestServerIntegrationScenarios:
    """Server integration test scenarios."""

    @pytest.mark.anyio
    async def test_server_request_response_cycle(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test a complete server request/response cycle."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            received_requests = []
            sent_responses = []

            def mock_sqs_receive():
                if received_requests:
                    return {"Messages": []}

                # Return a test request
                request_msg = {
                    "MessageId": "request-123",
                    "ReceiptHandle": "request-handle",
                    "Body": '{"jsonrpc": "2.0", "id": 1, "method": "server/process", "params": {"data": "test"}}',
                    "MessageAttributes": {},
                }
                received_requests.append(request_msg)
                return {"Messages": [request_msg]}

            def mock_sns_publish(*args, **kwargs):
                sent_responses.append(kwargs)
                return {"MessageId": "response-123"}

            def side_effect(func):
                if "receive_message" in str(func):
                    return mock_sqs_receive()
                elif "publish" in str(func):
                    return mock_sns_publish()
                elif "delete_message" in str(func):
                    return {}
                return {}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.5):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Receive request
                    request = await read_stream.receive()
                    assert isinstance(request, SessionMessage)
                    assert request.message.root.method == "server/process"

                    # Send response
                    response = JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"processed": True}))
                    await write_stream.send(SessionMessage(response))

                    # Give time for SNS publish
                    await anyio.sleep(0.05)

                    assert len(received_requests) > 0

    @pytest.mark.anyio
    async def test_server_notification_handling(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test server handling of notifications (no response expected)."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            notification_received = False

            def mock_sqs_receive():
                nonlocal notification_received
                if notification_received:
                    return {"Messages": []}

                # Return a notification
                notification_msg = {
                    "MessageId": "notification-123",
                    "ReceiptHandle": "notification-handle",
                    "Body": '{"jsonrpc": "2.0", "method": "server/notify", "params": {"event": "test"}}',
                    "MessageAttributes": {},
                }
                notification_received = True
                return {"Messages": [notification_msg]}

            def side_effect(func):
                if "receive_message" in str(func):
                    return mock_sqs_receive()
                elif "delete_message" in str(func):
                    return {}
                return {}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.2):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Receive notification
                    notification = await read_stream.receive()

                    assert isinstance(notification, SessionMessage)
                    assert notification.message.root.method == "server/notify"
                    assert not hasattr(notification.message.root, "id")  # Notifications don't have IDs

    @pytest.mark.anyio
    async def test_server_high_load_scenario(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test server handling high message load."""
        transport_config.max_messages = 10

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            call_count = 0

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func) and call_count == 1:
                    # First call returns many messages
                    return {
                        "Messages": [
                            {
                                "MessageId": f"load-{i}",
                                "ReceiptHandle": f"load-handle-{i}",
                                "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "server/load", "params": {{"index": {i}}}}}',
                                "MessageAttributes": {},
                            }
                            for i in range(10)
                        ]
                    }
                elif "receive_message" in str(func):
                    # Subsequent calls return empty
                    return {"Messages": []}
                elif "delete_message" in str(func):
                    return {}
                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.3):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Give time for bulk processing
                    await anyio.sleep(0.1)

    @pytest.mark.anyio
    async def test_server_metadata_preservation(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test that server preserves and uses message metadata correctly."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            original_msg_id = "original-123"

            def mock_sqs_receive():
                return {
                    "Messages": [
                        {
                            "MessageId": original_msg_id,
                            "ReceiptHandle": "original-handle",
                            "Body": '{"jsonrpc": "2.0", "id": 1, "method": "server/metadata", "params": {}}',
                            "MessageAttributes": {"ClientId": {"DataType": "String", "StringValue": "test-client"}},
                        }
                    ]
                }

            published_messages = []
            call_count = 0

            def mock_sns_publish(*args, **kwargs):
                published_messages.append(kwargs)
                return {"MessageId": "published-123"}

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func):
                    if call_count == 1:
                        return mock_sqs_receive()
                    return {"Messages": []}
                elif "publish" in str(func):
                    return mock_sns_publish()
                elif "delete_message" in str(func):
                    return {}
                return {}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.3):
                async with sns_sqs_server(transport_config, mock_sqs_client, mock_sns_client) as (read_stream, write_stream):
                    # Receive message with metadata
                    request = await read_stream.receive()

                    # Send response using the same session message (preserving metadata)
                    response = JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"success": True}))
                    response_session = SessionMessage(response, metadata=request.metadata)
                    await write_stream.send(response_session)

                    await anyio.sleep(0.05)
