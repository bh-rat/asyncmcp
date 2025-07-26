"""
Shared fixtures for SNS/SQS tests.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest

from asyncmcp.sns_sqs.utils import SnsSqsClientConfig, SnsSqsServerConfig


@pytest.fixture
def mock_sqs_client():
    """Mock SQS client for testing."""
    mock_client = MagicMock()
    mock_client.receive_message.return_value = {"Messages": []}
    mock_client.delete_message.return_value = {}
    mock_client.send_message.return_value = {"MessageId": "test-message-id"}
    return mock_client


@pytest.fixture
def mock_sns_client():
    """Mock SNS client for testing."""
    mock_client = MagicMock()
    mock_client.publish.return_value = {"MessageId": "test-sns-message-id"}
    return mock_client


@pytest.fixture
def sample_sqs_message():
    """Sample SQS message for testing."""
    return {
        "MessageId": "test-123",
        "ReceiptHandle": "handle-123",
        "Body": '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}',
        "MessageAttributes": {},
    }


@pytest.fixture
def sample_jsonrpc_request():
    """Sample JSON-RPC request message."""
    return JSONRPCMessage(root=JSONRPCRequest(method="test/method", params={"key": "value"}, jsonrpc="2.0", id=1))


@pytest.fixture
def sample_jsonrpc_notification():
    """Sample JSON-RPC notification message."""
    return JSONRPCMessage(root=JSONRPCNotification(method="test/notification", params={"event": "test"}, jsonrpc="2.0"))


@pytest.fixture
def server_config():
    """Server configuration using new SnsSqsServerConfig."""
    return SnsSqsServerConfig(
        sqs_queue_url="http://localhost:4566/000000000000/server-queue",
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,
    )


@pytest.fixture
def client_config():
    """Client configuration using new SnsSqsClientConfig."""
    return SnsSqsClientConfig(
        sqs_queue_url="http://localhost:4566/000000000000/client-queue",
        sns_topic_arn="arn:aws:sns:us-east-1:000000000000/server-requests",
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,
    )


@pytest.fixture
def client_server_config(client_config, server_config, mock_sqs_client, mock_sns_client):
    """Combined client and server configuration for integration tests."""
    return {
        "client": {"config": client_config, "sqs_client": mock_sqs_client, "sns_client": mock_sns_client},
        "server": {"config": server_config, "sqs_client": mock_sqs_client, "sns_client": mock_sns_client},
    }


@pytest.fixture
def mock_mcp_server():
    """Mock MCP server for session manager testing."""
    mock_server = MagicMock()
    mock_server.run = AsyncMock()
    mock_server.create_initialization_options = MagicMock(return_value={})
    return mock_server
