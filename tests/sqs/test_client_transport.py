"""
Comprehensive anyio fixture tests for SQS client transport module.
"""

import pytest
from unittest.mock import patch, MagicMock

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCNotification
from pydantic_core import ValidationError

from asyncmcp.sqs.utils import SqsTransportConfig, process_sqs_message
from asyncmcp.sqs.client import sqs_client, _create_sqs_message_attributes
from asyncmcp.sns_sqs.utils import _to_session_message, _delete_sqs_message

from .shared_fixtures import (
    mock_sqs_client,
    sample_sqs_message,
    sample_jsonrpc_request,
    sample_jsonrpc_notification,
    sample_jsonrpc_response,
    client_transport_config,
    server_transport_config,
    client_server_config,
)


@pytest.fixture
def transport_config(client_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return client_transport_config


class TestProcessSQSMessage:
    """Test the process_sqs_message function."""

    @pytest.mark.anyio
    async def test_process_direct_message(self, sample_sqs_message):
        """Test processing a direct SQS message."""
        session_message = await _to_session_message(sample_sqs_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1

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

    @pytest.mark.anyio
    async def test_process_multiple_messages(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing multiple messages concurrently."""
        messages = [sample_sqs_message.copy() for _ in range(3)]
        for i, msg in enumerate(messages):
            msg["MessageId"] = f"test-msg-{i}"
            msg["ReceiptHandle"] = f"test-handle-{i}"

        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await process_sqs_message(messages, mock_sqs_client, transport_config.read_queue_url, send_stream)

            # Verify all messages were processed
            assert mock_run_sync.call_count == 3  # One delete call per message

            # Verify messages were sent to stream
            processed_messages = []
            for _ in range(3):
                with anyio.move_on_after(0.1):
                    msg = await receive_stream.receive()
                    processed_messages.append(msg)

            assert len(processed_messages) == 3
            for msg in processed_messages:
                assert isinstance(msg, SessionMessage)

    @pytest.mark.anyio
    async def test_process_messages_with_errors(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing messages with validation errors."""
        valid_message = sample_sqs_message.copy()
        invalid_message = {
            "MessageId": "invalid-msg",
            "ReceiptHandle": "invalid-handle",
            "Body": "invalid json",
            "MessageAttributes": {},
        }

        messages = [valid_message, invalid_message]
        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await process_sqs_message(messages, mock_sqs_client, transport_config.read_queue_url, send_stream)

            # Both messages should be deleted even if one fails
            assert mock_run_sync.call_count == 2

            # Should receive one valid message and one exception
            results = []
            for _ in range(2):
                with anyio.move_on_after(0.1):
                    result = await receive_stream.receive()
                    results.append(result)

            assert len(results) == 2
            valid_results = [r for r in results if isinstance(r, SessionMessage)]
            error_results = [r for r in results if isinstance(r, Exception)]
            assert len(valid_results) == 1
            assert len(error_results) == 1


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


class TestCreateSQSMessageAttributes:
    """Test the _create_sqs_message_attributes function."""

    @pytest.mark.anyio
    async def test_create_attributes_for_request(self, transport_config, sample_jsonrpc_request):
        """Test creating message attributes for a request."""
        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await _create_sqs_message_attributes(session_message, "test-client", transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "test-client"
        assert attrs["MessageType"]["DataType"] == "String"
        assert attrs["ClientId"]["DataType"] == "String"

    @pytest.mark.anyio
    async def test_create_attributes_for_notification(self, transport_config, sample_jsonrpc_notification):
        """Test creating message attributes for a notification."""
        session_message = SessionMessage(sample_jsonrpc_notification)
        attrs = await _create_sqs_message_attributes(session_message, "test-client", transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "test-client"

    @pytest.mark.anyio
    async def test_create_attributes_with_custom_config(self, transport_config, sample_jsonrpc_request):
        """Test creating message attributes with custom configuration."""
        transport_config.message_attributes = {"CustomAttr": {"DataType": "String", "StringValue": "custom"}}
        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await _create_sqs_message_attributes(session_message, "test-client", transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "test-client"
        assert attrs["CustomAttr"]["StringValue"] == "custom"


class TestSqsClient:
    """Test the sqs_client function."""

    @pytest.mark.anyio
    async def test_context_manager_basic(self, transport_config, mock_sqs_client):
        """Test basic context manager functionality."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            with anyio.move_on_after(0.1):  # Add timeout to prevent hanging
                async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    assert read_stream is not None
                    assert write_stream is not None
                    # Give the background tasks a moment to start
                    await anyio.sleep(0.01)

    @pytest.mark.anyio
    async def test_send_and_receive_messages(self, transport_config, sample_jsonrpc_request, mock_sqs_client):
        """Test sending and receiving messages through the client."""
        session_message = SessionMessage(sample_jsonrpc_request)

        # Mock SQS client methods directly
        mock_sqs_client.send_message.return_value = {"MessageId": "test-msg-id"}
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.2):  # Add timeout to prevent hanging
            async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Send a message
                await write_stream.send(session_message)
                await anyio.sleep(0.01)  # Allow message to be processed

                # Verify send_message was called
                assert mock_sqs_client.send_message.called

    @pytest.mark.anyio
    async def test_timeout_handling(self, transport_config, mock_sqs_client):
        """Test timeout handling in the client."""
        transport_config.transport_timeout_seconds = 0.1

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            with anyio.move_on_after(0.2):
                async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    await anyio.sleep(0.05)  # Should not timeout

    @pytest.mark.anyio
    async def test_error_handling_in_sqs_reader(self, transport_config, mock_sqs_client):
        """Test error handling in SQS reader task."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:

            def side_effect(func):
                if "receive_message" in str(func):
                    raise Exception("SQS receive error")
                return {"Messages": []}

            mock_run_sync.side_effect = side_effect

            with anyio.move_on_after(0.1):
                async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    # Should handle the error gracefully
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_error_handling_in_sqs_writer(self, transport_config, sample_jsonrpc_request, mock_sqs_client):
        """Test error handling in SQS writer task."""
        session_message = SessionMessage(sample_jsonrpc_request)

        with patch("anyio.to_thread.run_sync") as mock_run_sync:

            def side_effect(func):
                if "send_message" in str(func):
                    raise Exception("SQS send error")
                elif "receive_message" in str(func):
                    return {"Messages": []}
                return {}

            mock_run_sync.side_effect = side_effect

            with anyio.move_on_after(0.1):
                async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    # Should handle the error gracefully
                    await write_stream.send(session_message)
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_concurrent_message_processing(self, transport_config, mock_sqs_client):
        """Test concurrent message processing."""
        # Mock SQS client methods directly
        # Return empty messages first, then return the messages we want to process
        call_count = 0

        def receive_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": f"msg-{i}",
                            "ReceiptHandle": f"handle-{i}",
                            "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "test", "params": {{}}}}',
                            "MessageAttributes": {},
                        }
                        for i in range(3)
                    ]
                }
            else:
                return {"Messages": []}

        mock_sqs_client.receive_message.side_effect = receive_side_effect
        mock_sqs_client.send_message.return_value = {"MessageId": "test-msg-id"}

        with anyio.move_on_after(0.5):  # Add timeout to prevent hanging
            async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Should process multiple messages concurrently
                messages = []
                for _ in range(3):
                    with anyio.move_on_after(0.1):
                        msg = await read_stream.receive()
                        if msg:
                            messages.append(msg)

                assert len(messages) == 3
                for msg in messages:
                    assert isinstance(msg, SessionMessage)

    @pytest.mark.anyio
    async def test_stream_cleanup(self, transport_config, mock_sqs_client):
        """Test proper cleanup of streams."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            read_stream = None
            write_stream = None

            with anyio.move_on_after(0.1):  # Add timeout to prevent hanging
                async with sqs_client(transport_config, mock_sqs_client) as (rs, ws):
                    read_stream = rs
                    write_stream = ws
                    assert read_stream is not None
                    assert write_stream is not None
                    await anyio.sleep(0.01)  # Give background tasks time to start

            # Streams should be properly closed after context exit
            # This is implicit in the anyio stream implementation


class TestConfigurationValidation:
    """Test configuration validation."""

    def test_config_creation(self, mock_sqs_client):
        """Test basic configuration creation."""
        config = SqsTransportConfig(
            read_queue_url="http://localhost:4566/000000000000/read-queue",
            write_queue_url="http://localhost:4566/000000000000/write-queue",
        )

        assert config.read_queue_url == "http://localhost:4566/000000000000/read-queue"
        assert config.write_queue_url == "http://localhost:4566/000000000000/write-queue"
        assert config.max_messages == 10  # Default value
        assert config.wait_time_seconds == 20  # Default value
        assert config.poll_interval_seconds == 5.0  # Default value

    def test_config_with_custom_values(self, mock_sqs_client):
        """Test configuration with custom values."""
        config = SqsTransportConfig(
            read_queue_url="http://localhost:4566/000000000000/read-queue",
            write_queue_url="http://localhost:4566/000000000000/write-queue",
            max_messages=5,
            wait_time_seconds=10,
            poll_interval_seconds=1.0,
            client_id="custom-client",
            transport_timeout_seconds=30.0,
        )

        assert config.max_messages == 5
        assert config.wait_time_seconds == 10
        assert config.poll_interval_seconds == 1.0
        assert config.client_id == "custom-client"
        assert config.transport_timeout_seconds == 30.0


class TestIntegrationScenarios:
    """Test integration scenarios."""

    @pytest.mark.anyio
    async def test_full_message_roundtrip(self, transport_config, mock_sqs_client):
        """Test full message roundtrip scenario."""
        # Mock SQS client methods directly
        mock_sqs_client.receive_message.return_value = {"Messages": []}
        mock_sqs_client.send_message.return_value = {"MessageId": "test-msg-id"}

        with anyio.move_on_after(0.2):  # Add timeout to prevent hanging
            async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Send a request
                request = SessionMessage(
                    JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test", params={}))
                )
                await write_stream.send(request)
                await anyio.sleep(0.01)

                # Verify message was sent
                assert mock_sqs_client.send_message.called

    @pytest.mark.anyio
    async def test_high_throughput_scenario(self, transport_config, mock_sqs_client):
        """Test high throughput message processing."""
        # Mock SQS client methods directly
        call_count = 0

        def receive_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": f"msg-{i}",
                            "ReceiptHandle": f"handle-{i}",
                            "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "test", "params": {{}}}}',
                            "MessageAttributes": {},
                        }
                        for i in range(10)
                    ]
                }
            else:
                return {"Messages": []}

        mock_sqs_client.receive_message.side_effect = receive_side_effect
        mock_sqs_client.send_message.return_value = {"MessageId": "test-msg-id"}

        with anyio.move_on_after(1.0):  # Add timeout to prevent hanging
            async with sqs_client(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Process multiple messages
                messages = []
                for _ in range(10):
                    with anyio.move_on_after(0.1):
                        msg = await read_stream.receive()
                        if msg:
                            messages.append(msg)

                assert len(messages) == 10
                for msg in messages:
                    assert isinstance(msg, SessionMessage)
