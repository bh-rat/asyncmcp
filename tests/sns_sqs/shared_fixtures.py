import json
import pytest
from unittest.mock import MagicMock

from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification

from asyncmcp import SnsSqsTransportConfig


@pytest.fixture
def mock_sqs_client():
    """Mock SQS client for testing."""
    client = MagicMock()
    client.receive_message = MagicMock()
    client.delete_message = MagicMock()
    return client


@pytest.fixture
def mock_sns_client():
    """Mock SNS client for testing."""
    client = MagicMock()
    client.publish = MagicMock()
    return client


@pytest.fixture
def sample_jsonrpc_request():
    """Sample JSON-RPC request message."""
    return JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/method", params={"key": "value"}))


@pytest.fixture
def sample_jsonrpc_response():
    """Sample JSON-RPC response message."""
    return JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"status": "success"}))


@pytest.fixture
def sample_jsonrpc_notification():
    """Sample JSON-RPC notification message."""
    return JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method="test/notification", params={"event": "test"}))


@pytest.fixture
def sample_sqs_message():
    """Sample SQS message structure."""
    return {
        "MessageId": "test-msg-123",
        "ReceiptHandle": "test-receipt-handle",
        "Body": '{"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {"key": "value"}}',
        "MessageAttributes": {"ClientId": {"DataType": "String", "StringValue": "test-client"}},
    }


@pytest.fixture
def sample_sns_wrapped_message():
    """Sample SQS message that contains SNS notification."""
    sns_body = {
        "Type": "Notification",
        "Message": '{"jsonrpc": "2.0", "id": 2, "method": "test/notification", "params": {"event": "test"}}',
    }
    return {
        "MessageId": "test-msg-456",
        "ReceiptHandle": "test-receipt-handle-2",
        "Body": json.dumps(sns_body),
        "MessageAttributes": {},
    }


@pytest.fixture
def client_transport_config():
    """Create a test client transport configuration."""
    return SnsSqsTransportConfig(
        sqs_queue_url="http://localhost:4566/000000000000/client-queue",
        sns_topic_arn="arn:aws:sns:us-east-1:000000000000/client-topic",
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
        client_id="test-client",
        transport_timeout_seconds=None,
    )


@pytest.fixture
def server_transport_config():
    """Create a test server transport configuration."""
    return SnsSqsTransportConfig(
        sqs_queue_url="http://localhost:4566/000000000000/server-queue",
        sns_topic_arn="arn:aws:sns:us-east-1:000000000000/server-topic",
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
    )


@pytest.fixture
def client_server_config(mock_sqs_client, mock_sns_client):
    """Create client and server configurations for integration testing."""
    mock_client_sqs = MagicMock()
    mock_client_sns = MagicMock()
    mock_server_sqs = MagicMock()
    mock_server_sns = MagicMock()

    client_config = SnsSqsTransportConfig(
        sqs_queue_url="http://localhost:4566/000000000000/client-responses",
        sns_topic_arn="arn:aws:sns:us-east-1:000000000000/server-requests",
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
        client_id="test-client",
    )

    server_config = SnsSqsTransportConfig(
        sqs_queue_url="http://localhost:4566/000000000000/server-requests",
        sns_topic_arn="arn:aws:sns:us-east-1:000000000000/client-responses",
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
    )

    return {
        "client": {"config": client_config, "sqs_client": mock_client_sqs, "sns_client": mock_client_sns},
        "server": {"config": server_config, "sqs_client": mock_server_sqs, "sns_client": mock_server_sns},
    }
