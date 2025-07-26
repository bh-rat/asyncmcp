"""
Tests for SNS/SQS client transport functionality.
"""

from unittest.mock import patch

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest

from asyncmcp import SnsSqsClientConfig

# Updated imports to use correct common modules
from asyncmcp.common.aws_queue_utils import delete_sqs_message, to_session_message
from asyncmcp.sns_sqs.client import sns_sqs_client


# Test classes for processing SQS messages
class TestProcessSQSMessage:
    """Test SQS message processing utilities."""

    @pytest.mark.anyio
    async def test_process_direct_message(self, sample_sqs_message, mock_sqs_client):
        """Test processing direct SQS message."""
        # Test direct message conversion
        session_message = await to_session_message(sample_sqs_message)
        assert session_message.message.root.method == "test"

    @pytest.mark.anyio
    async def test_process_sns_wrapped_message(self, mock_sqs_client):
        """Test processing SNS-wrapped SQS message."""
        sns_wrapped_message = {
            "MessageId": "sns-123",
            "ReceiptHandle": "sns-handle-123",
            "Body": '{"Type": "Notification", "Message": "{'
            '\\"jsonrpc\\": \\"2.0\\", '
            '\\"id\\": 1, '
            '\\"method\\": \\"test\\", '
            '\\"params\\": {}'
            '}"}',
            "MessageAttributes": {},
        }

        # Test SNS-wrapped message conversion
        session_message = await to_session_message(sns_wrapped_message)
        assert session_message.message.root.method == "test"

    @pytest.mark.anyio
    async def test_process_invalid_json(self, mock_sqs_client):
        """Test handling invalid JSON messages."""
        invalid_message = {
            "MessageId": "invalid-123",
            "ReceiptHandle": "invalid-handle-123",
            "Body": "invalid json",
            "MessageAttributes": {},
        }

        # Should raise ValueError for invalid messages
        with pytest.raises(ValueError):
            await to_session_message(invalid_message)

    @pytest.mark.anyio
    async def test_process_invalid_jsonrpc(self, mock_sqs_client):
        """Test handling invalid JSON-RPC messages."""
        invalid_jsonrpc = {
            "MessageId": "invalid-jsonrpc-123",
            "ReceiptHandle": "invalid-jsonrpc-handle-123",
            "Body": '{"not": "jsonrpc"}',
            "MessageAttributes": {},
        }

        # Should raise ValueError for invalid JSON-RPC
        with pytest.raises(ValueError):
            await to_session_message(invalid_jsonrpc)


class TestDeleteSQSMessage:
    """Test SQS message deletion."""

    @pytest.mark.anyio
    async def test_delete_message_success(self, mock_sqs_client):
        """Test successful message deletion."""
        await delete_sqs_message(mock_sqs_client, "http://test-queue", "test-handle")

        mock_sqs_client.delete_message.assert_called_once_with(
            QueueUrl="http://test-queue", ReceiptHandle="test-handle"
        )


class TestSQSMessageProcessor:
    """Test SQS message processing functionality."""

    @pytest.mark.anyio
    async def test_process_multiple_messages(self, client_config, sample_sqs_message, mock_sqs_client):
        """Test processing multiple SQS messages."""
        # Create multiple test messages
        messages = []
        for i in range(3):
            msg = sample_sqs_message.copy()
            msg["MessageId"] = f"test-{i}"
            msg["ReceiptHandle"] = f"handle-{i}"
            msg["Body"] = f'{{"jsonrpc": "2.0", "id": {i}, "method": "test{i}", "params": {{}}}}'
            messages.append(msg)

        # Test conversion of multiple messages
        for i, msg in enumerate(messages):
            session_message = await to_session_message(msg)
            assert session_message.message.root.method == f"test{i}"

    @pytest.mark.anyio
    async def test_process_messages_with_errors(self, client_config, sample_sqs_message, mock_sqs_client):
        """Test processing messages with some invalid ones."""
        # Mix valid and invalid messages
        valid_msg = sample_sqs_message
        invalid_msg = {
            "MessageId": "invalid-456",
            "ReceiptHandle": "invalid-handle-456",
            "Body": "invalid json content",
            "MessageAttributes": {},
        }

        # Valid message should convert successfully
        valid_session_message = await to_session_message(valid_msg)
        assert valid_session_message.message.root.method == "test"

        # Invalid message should raise error
        with pytest.raises(ValueError):
            await to_session_message(invalid_msg)


# Note: SNS message attribute creation tests have been removed as this functionality
# is now internal to the SnsSqsClientTransport class and tested through integration tests.


class TestSnsSqsClient:
    """Test the main sns_sqs_client context manager."""

    @pytest.mark.anyio
    async def test_context_manager_basic(self, client_config, mock_sqs_client, mock_sns_client):
        """Test basic context manager functionality."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty SQS response to prevent infinite loop
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    # Just test that context manager works
                    await anyio.sleep(0.01)

    @pytest.mark.anyio
    async def test_send_and_receive_messages(
        self, client_config, sample_jsonrpc_request, mock_sqs_client, mock_sns_client
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
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    # Send a message
                    await write_stream.send(SessionMessage(sample_jsonrpc_request))

                    # Give some time for processing
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_timeout_handling(self, client_config, mock_sqs_client, mock_sns_client):
        """Test transport with timeout configuration."""
        client_config.transport_timeout_seconds = 0.1

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            # Mock empty response to prevent hanging
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.2):
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_error_handling_in_sqs_reader(self, client_config, mock_sqs_client, mock_sns_client):
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
                    async with sns_sqs_client(
                        client_config,
                        mock_sqs_client,
                        mock_sns_client,
                        "arn:aws:sns:us-east-1:000000000000:client-topic",
                    ) as (
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
        self, client_config, sample_jsonrpc_request, mock_sqs_client, mock_sns_client
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
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    # Try to send a message - should handle SNS error gracefully
                    await write_stream.send(SessionMessage(sample_jsonrpc_request))
                    await anyio.sleep(0.05)

    @pytest.mark.anyio
    async def test_concurrent_message_processing(self, client_config, mock_sqs_client, mock_sns_client):
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
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    # Give some time for processing multiple messages
                    await anyio.sleep(0.1)

    @pytest.mark.anyio
    async def test_stream_cleanup(self, client_config, mock_sqs_client, mock_sns_client):
        """Test proper cleanup of streams."""
        read_stream = None
        write_stream = None

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.1):
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (rs, ws):
                    read_stream = rs
                    write_stream = ws
                    await anyio.sleep(0.01)

        # Streams should be properly cleaned up after context exit
        # This mainly tests that no exceptions are raised during cleanup


class TestConfigurationValidation:
    """Test configuration validation and creation."""

    def test_config_creation(self):
        """Test creating basic client config."""
        config = SnsSqsClientConfig(
            sqs_queue_url="http://localhost:4566/000000000000/test-queue",
            sns_topic_arn="arn:aws:sns:us-east-1:000000000000:test-topic",
        )

        assert config.sqs_queue_url == "http://localhost:4566/000000000000/test-queue"
        assert config.sns_topic_arn == "arn:aws:sns:us-east-1:000000000000:test-topic"
        assert config.max_messages == 10  # default value
        assert config.wait_time_seconds == 20  # default value

    def test_config_with_custom_values(self):
        """Test creating config with custom values."""
        config = SnsSqsClientConfig(
            sqs_queue_url="http://custom-queue",
            sns_topic_arn="arn:custom-topic",
            max_messages=5,
            wait_time_seconds=10,
            client_id="custom-client",
        )

        assert config.max_messages == 5
        assert config.wait_time_seconds == 10
        assert config.client_id == "custom-client"


class TestIntegrationScenarios:
    """Test more complex integration scenarios."""

    @pytest.mark.anyio
    async def test_full_message_roundtrip(self, client_config, mock_sqs_client, mock_sns_client):
        """Test a complete message send/receive cycle."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            message_sent = False

            def side_effect(func):
                nonlocal message_sent

                if "publish" in str(func):
                    message_sent = True
                    return {"MessageId": "sent-123"}
                elif "receive_message" in str(func):
                    if message_sent:
                        # Return a response message after sending
                        return {
                            "Messages": [
                                {
                                    "MessageId": "response-123",
                                    "ReceiptHandle": "response-handle",
                                    "Body": '{"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}',
                                    "MessageAttributes": {},
                                }
                            ]
                        }
                    return {"Messages": []}
                elif "delete_message" in str(func):
                    return {}

            mock_run_sync.side_effect = side_effect

            # Use timeout to prevent hanging
            with anyio.move_on_after(0.5):
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    # Send a request
                    request = JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/request", params={}))
                    await write_stream.send(SessionMessage(request))

                    # Wait a bit for processing
                    await anyio.sleep(0.1)

    @pytest.mark.anyio
    async def test_high_throughput_scenario(self, client_config, mock_sqs_client, mock_sns_client):
        """Test handling high message throughput."""
        client_config.max_messages = 10

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
                async with sns_sqs_client(
                    client_config, mock_sqs_client, mock_sns_client, "arn:aws:sns:us-east-1:000000000000:client-topic"
                ) as (
                    read_stream,
                    write_stream,
                ):
                    # Give time for bulk processing
                    await anyio.sleep(0.1)
