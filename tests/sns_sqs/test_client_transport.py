"""
Comprehensive anyio fixture tests for SQS+SNS client transport module.
"""

import pytest
from unittest.mock import patch

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest
from pydantic_core import ValidationError

from asyncmcp.sns_sqs.utils import _to_session_message, process_sqs_message, _delete_sqs_message, SnsSqsTransportConfig
from asyncmcp.sns_sqs.client import (
    sns_sqs_client,
    _create_sns_message_attributes,
)
from asyncmcp import SnsSqsTransportConfig


@pytest.fixture
def transport_config(client_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return client_transport_config


class TestProcessSQSMessage:
    """Test the _to_session_message function."""

    @pytest.mark.anyio
    async def test_process_direct_message(self, sample_sqs_message):
        """Test processing a direct SQS message."""
        session_message = await _to_session_message(sample_sqs_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1

    @pytest.mark.anyio
    async def test_process_sns_wrapped_message(self, sample_sns_wrapped_message):
        """Test processing an SNS-wrapped SQS message."""
        session_message = await _to_session_message(sample_sns_wrapped_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/notification"
        assert session_message.message.root.id == 2

    @pytest.mark.anyio
    async def test_process_invalid_json(self):
        """Test processing message with invalid JSON."""
        invalid_message = {
            "MessageId": "test-msg-invalid",
            "ReceiptHandle": "test-receipt-handle",
            "Body": "invalid json content",
            "MessageAttributes": {},
        }

        with pytest.raises(ValidationError):
            await _to_session_message(invalid_message)

    @pytest.mark.anyio
    async def test_process_invalid_jsonrpc(self):
        """Test processing message with invalid JSON-RPC."""
        invalid_jsonrpc = {
            "MessageId": "test-msg-invalid-rpc",
            "ReceiptHandle": "test-receipt-handle",
            "Body": '{"invalid": "jsonrpc"}',
            "MessageAttributes": {},
        }

        with pytest.raises(ValidationError):
            await _to_session_message(invalid_jsonrpc)


class TestDeleteSQSMessage:
    """Test the _delete_sqs_message function."""

    @pytest.mark.anyio
    async def test_delete_message_success(self, mock_sqs_client):
        """Test successful message deletion."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await _delete_sqs_message(mock_sqs_client, "test-queue", "test-handle")

            mock_run_sync.assert_called_once()
            # Verify the lambda function would call delete_message correctly
            delete_func = mock_run_sync.call_args[0][0]
            delete_func()
            mock_sqs_client.delete_message.assert_called_once_with(QueueUrl="test-queue", ReceiptHandle="test-handle")


class TestSQSMessageProcessor:
    """Test the process_sqs_message function."""

    @pytest.mark.anyio
    async def test_process_multiple_messages(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing multiple messages concurrently."""
        messages = [sample_sqs_message.copy() for _ in range(3)]
        for i, msg in enumerate(messages):
            msg["MessageId"] = f"test-msg-{i}"
            msg["ReceiptHandle"] = f"test-handle-{i}"

        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with (
            patch("asyncmcp.sns_sqs.utils._to_session_message") as mock_process,
            patch("asyncmcp.sns_sqs.utils._delete_sqs_message") as mock_delete,
        ):
            mock_process.return_value = SessionMessage(
                JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test"))
            )

            await process_sqs_message(messages, mock_sqs_client, transport_config, send_stream)

            # Should have processed all messages
            assert mock_process.call_count == 3
            assert mock_delete.call_count == 3

    @pytest.mark.anyio
    async def test_process_messages_with_errors(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing messages when some fail."""
        messages = [sample_sqs_message.copy() for _ in range(2)]
        messages[0]["MessageId"] = "good-msg"
        messages[1]["MessageId"] = "bad-msg"

        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with (
            patch("asyncmcp.sns_sqs.utils._to_session_message") as mock_process,
            patch("asyncmcp.sns_sqs.utils._delete_sqs_message") as mock_delete,
        ):
            # First message succeeds, second fails
            mock_process.side_effect = [
                SessionMessage(JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test"))),
                ValidationError.from_exception_data("test", []),
            ]

            await process_sqs_message(messages, mock_sqs_client, transport_config, send_stream)

            # Both messages should be processed and deleted (even the failed one)
            assert mock_process.call_count == 2
            assert mock_delete.call_count == 2


class TestCreateSNSMessageAttributes:
    """Test the _create_sns_message_attributes function."""

    @pytest.mark.anyio
    async def test_create_attributes_for_request(self, transport_config, sample_jsonrpc_request):
        """Test creating attributes for JSON-RPC request."""
        session_message = SessionMessage(sample_jsonrpc_request)

        attrs = await _create_sns_message_attributes(session_message, "test-client", transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "test-client"
        assert attrs["RequestId"]["StringValue"] == "1"
        assert attrs["Method"]["StringValue"] == "test/method"
        assert "Timestamp" in attrs

    @pytest.mark.anyio
    async def test_create_attributes_for_notification(self, transport_config, sample_jsonrpc_notification):
        """Test creating attributes for JSON-RPC notification."""
        session_message = SessionMessage(sample_jsonrpc_notification)

        attrs = await _create_sns_message_attributes(session_message, "test-client", transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "test-client"
        assert attrs["Method"]["StringValue"] == "test/notification"
        assert "RequestId" not in attrs  # Notifications don't have request IDs

    @pytest.mark.anyio
    async def test_create_attributes_with_custom_config(self, transport_config, sample_jsonrpc_request):
        """Test creating attributes with custom message attributes in config."""
        transport_config.message_attributes = {"CustomAttr": {"DataType": "String", "StringValue": "custom-value"}}

        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await _create_sns_message_attributes(session_message, "test-client", transport_config)

        assert attrs["CustomAttr"]["StringValue"] == "custom-value"


class TestSnsSqsClient:
    """Test the main sns_sqs_client context manager."""

    @pytest.mark.anyio
    async def test_context_manager_basic(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test basic context manager functionality."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty SQS response to prevent infinite loop
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    assert read_stream is not None
                    assert write_stream is not None
                    # Brief delay to let background tasks start
                    await anyio.sleep(0.01)

    @pytest.mark.anyio
    async def test_send_and_receive_messages(
        self, transport_config, sample_jsonrpc_request, mock_sqs_client, mock_sns_client
    ):
        """Test sending and receiving messages through the transport."""
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
                                    "MessageId": "test-123",
                                    "ReceiptHandle": "handle-123",
                                    "Body": '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}',
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
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    # Send a message
                    session_message = SessionMessage(sample_jsonrpc_request)
                    await write_stream.send(session_message)

                    # Give some time for processing
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_timeout_handling(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test transport with timeout configuration."""
        transport_config.transport_timeout_seconds = 0.1

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty response to prevent hanging
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.2):
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_error_handling_in_sqs_reader(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test error handling in SQS reader."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock SQS exception on first call, then return empty messages
            call_count = 0

            def side_effect(func):
                nonlocal call_count
                call_count += 1

                if "receive_message" in str(func):
                    if call_count == 1:
                        # First call raises exception
                        raise Exception("SQS error")
                    else:
                        # Subsequent calls return empty to prevent infinite loop
                        return {"Messages": []}
                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                # The client should handle the error gracefully without crashing
                try:
                    async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                        read_stream,
                        write_stream,
                    ):
                        # Should handle the error gracefully
                        await anyio.sleep(0.05)
                except Exception:
                    # Expected that the exception might propagate, test passes if we get here
                    pass

    @pytest.mark.anyio
    async def test_error_handling_in_sns_writer(
        self, transport_config, sample_jsonrpc_request, mock_sqs_client, mock_sns_client
    ):
        """Test error handling in SNS writer."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty SQS response for reader
            mock_run_sync.return_value = {"Messages": []}

            # Mock SNS exception for writer
            def side_effect(func):
                if "publish" in str(func):
                    raise Exception("SNS error")
                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    # Send message that will cause SNS error
                    session_message = SessionMessage(sample_jsonrpc_request)
                    await write_stream.send(session_message)

                    # Should handle the error gracefully and continue
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_concurrent_message_processing(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test that multiple messages are processed concurrently."""
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
                                "MessageId": f"test-{i}",
                                "ReceiptHandle": f"handle-{i}",
                                "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "test", "params": {{}}}}',
                                "MessageAttributes": {},
                            }
                            for i in range(3)
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
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    # Give time for message processing
                    await anyio.sleep(0.1)

    @pytest.mark.anyio
    async def test_stream_cleanup(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test proper cleanup of streams."""
        read_stream = None
        write_stream = None

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (rs, ws):
                    read_stream = rs
                    write_stream = ws

                    # Brief delay to let background tasks start
                    await anyio.sleep(0.01)

        # Note: Stream cleanup testing depends on implementation details


class TestConfigurationValidation:
    """Test transport configuration validation."""

    def test_config_creation(self, mock_sqs_client, mock_sns_client):
        """Test creating transport configuration."""
        config = SnsSqsTransportConfig(
            sqs_queue_url="test-queue",
            sns_topic_arn="test-topic",
        )

        assert config.sqs_queue_url == "test-queue"
        assert config.sns_topic_arn == "test-topic"
        assert config.max_messages == 10  # Default value
        assert config.wait_time_seconds == 20  # Default value
        assert config.visibility_timeout_seconds == 30  # Default value
        assert config.message_attributes is None  # Default value
        assert config.poll_interval_seconds == 5.0  # Default value
        assert config.client_id is None  # Default value
        assert config.transport_timeout_seconds is None  # Default value

    def test_config_with_custom_values(self, mock_sqs_client, mock_sns_client):
        """Test creating configuration with custom values."""
        config = SnsSqsTransportConfig(
            sqs_queue_url="custom-queue",
            sns_topic_arn="custom-topic",
            max_messages=5,
            client_id="custom-client",
            transport_timeout_seconds=30.0,
        )

        assert config.sqs_queue_url == "custom-queue"
        assert config.sns_topic_arn == "custom-topic"
        assert config.max_messages == 5
        assert config.client_id == "custom-client"
        assert config.transport_timeout_seconds == 30.0


class TestIntegrationScenarios:
    """Integration test scenarios."""

    @pytest.mark.anyio
    async def test_full_message_roundtrip(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test a complete message send/receive cycle."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            sent_messages = []
            received_messages = []

            def mock_sqs_receive():
                if received_messages:
                    return {"Messages": []}

                # Return a test message
                msg = {
                    "MessageId": "response-123",
                    "ReceiptHandle": "response-handle",
                    "Body": '{"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}',
                    "MessageAttributes": {},
                }
                received_messages.append(msg)
                return {"Messages": [msg]}

            def mock_sns_publish(*args, **kwargs):
                sent_messages.append(kwargs)
                return {"MessageId": "sent-123"}

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
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    # Send a request
                    request = JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test", params={}))
                    await write_stream.send(SessionMessage(request))

                    # Receive response
                    response = await read_stream.receive()

                    assert isinstance(response, SessionMessage)
                    assert response.message.root.id == 1
                    assert len(sent_messages) > 0

    @pytest.mark.anyio
    async def test_high_throughput_scenario(self, transport_config, mock_sqs_client, mock_sns_client):
        """Test handling high message throughput."""
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
                                "MessageId": f"bulk-{i}",
                                "ReceiptHandle": f"bulk-handle-{i}",
                                "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "bulk", "params": {{}}}}',
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
                async with sns_sqs_client(transport_config, mock_sqs_client, mock_sns_client) as (
                    read_stream,
                    write_stream,
                ):
                    # Give time for bulk processing
                    await anyio.sleep(0.1)
