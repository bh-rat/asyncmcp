import json
import pytest
from unittest.mock import MagicMock

from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification

from asyncmcp.sqs.utils import SqsTransportConfig


@pytest.fixture
def mock_sqs_client():
    """Mock SQS client for testing."""
    client = MagicMock()
    client.receive_message = MagicMock()
    client.delete_message = MagicMock()
    client.send_message = MagicMock()
    return client


@pytest.fixture
def sample_jsonrpc_request():
    """Sample JSON-RPC request message."""
    return JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/method", params={"key": "value"}))


@pytest.fixture
def sample_jsonrpc_initialize_request():
    """Sample JSON-RPC initialize request with response_queue_url."""
    return JSONRPCMessage(
        root=JSONRPCRequest(
            jsonrpc="2.0",
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "response_queue_url": "http://localhost:4566/000000000000/client-responses",
            },
        )
    )


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
        "MessageId": "msg-1",
        "ReceiptHandle": "handle-1",
        "Body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {"key": "value"}}),
        "MessageAttributes": {"ClientId": {"DataType": "String", "StringValue": "test-client"}},
    }


@pytest.fixture
def sample_initialize_sqs_message():
    """Sample SQS initialize message with response_queue_url."""
    return {
        "MessageId": "init-msg-1",
        "ReceiptHandle": "init-handle-1",
        "Body": json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                    "response_queue_url": "http://localhost:4566/000000000000/client-responses",
                },
            }
        ),
        "MessageAttributes": {"Method": {"DataType": "String", "StringValue": "initialize"}},
    }


@pytest.fixture
def sample_sqs_notification():
    """Sample SQS notification message."""
    return {
        "MessageId": "notif-1",
        "ReceiptHandle": "notif-handle-1",
        "Body": json.dumps({"jsonrpc": "2.0", "method": "test/notification", "params": {"event": "test"}}),
        "MessageAttributes": {},
    }


@pytest.fixture
def sample_sqs_response():
    """Sample SQS response message."""
    return {
        "MessageId": "resp-1",
        "ReceiptHandle": "resp-handle-1",
        "Body": json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"status": "success"}}),
        "MessageAttributes": {},
    }


@pytest.fixture
def client_transport_config():
    """Create a test client transport configuration (no write_queue_url)."""
    return SqsTransportConfig(
        read_queue_url="http://localhost:4566/000000000000/server-requests",  # Client sends to server queue
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
        client_id="test-client",
        transport_timeout_seconds=None,
    )


@pytest.fixture
def client_response_queue_url():
    """Client's response queue URL for dynamic queue system."""
    return "http://localhost:4566/000000000000/client-responses"


@pytest.fixture
def server_transport_config():
    """Create a test server transport configuration (no write_queue_url)."""
    return SqsTransportConfig(
        read_queue_url="http://localhost:4566/000000000000/server-requests",  # Server reads from this queue
        max_messages=10,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
    )


@pytest.fixture
def client_server_config():
    """Create client and server configurations for integration testing."""
    mock_client_sqs = MagicMock()
    mock_server_sqs = MagicMock()

    client_config = SqsTransportConfig(
        read_queue_url="http://localhost:4566/000000000000/server-requests",  # Client sends to server queue
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
        client_id="test-client",
    )

    server_config = SqsTransportConfig(
        read_queue_url="http://localhost:4566/000000000000/server-requests",  # Server reads requests
        max_messages=10,
        wait_time_seconds=1,
        poll_interval_seconds=0.01,  # Faster polling for tests
    )

    return {
        "client": {
            "config": client_config,
            "sqs_client": mock_client_sqs,
            "response_queue_url": "http://localhost:4566/000000000000/client-responses",
        },
        "server": {"config": server_config, "sqs_client": mock_server_sqs},
    }


@pytest.fixture
def invalid_sqs_message():
    """Sample invalid SQS message for error testing."""
    return {
        "MessageId": "invalid-msg-1",
        "ReceiptHandle": "invalid-handle-1",
        "Body": "invalid json content",
        "MessageAttributes": {},
    }


@pytest.fixture
def bulk_sqs_messages():
    """Sample bulk SQS messages for high-throughput testing."""
    return [
        {
            "MessageId": f"bulk-msg-{i}",
            "ReceiptHandle": f"bulk-handle-{i}",
            "Body": json.dumps({"jsonrpc": "2.0", "id": i, "method": "bulk/test", "params": {"index": i}}),
            "MessageAttributes": {"BatchIndex": {"DataType": "Number", "StringValue": str(i)}},
        }
        for i in range(10)
    ]


@pytest.fixture
def custom_message_attributes():
    """Custom message attributes for testing."""
    return {
        "CustomAttr": {"DataType": "String", "StringValue": "custom-value"},
        "Priority": {"DataType": "Number", "StringValue": "1"},
        "Environment": {"DataType": "String", "StringValue": "test"},
    }


@pytest.fixture
def timeout_transport_config():
    """Transport configuration with timeout for testing (no write_queue_url)."""
    return SqsTransportConfig(
        read_queue_url="http://localhost:4566/000000000000/timeout-queue",
        poll_interval_seconds=0.01,
        transport_timeout_seconds=0.1,  # Short timeout for testing
    )


@pytest.fixture
def high_throughput_config():
    """Configuration optimized for high throughput testing (no write_queue_url)."""
    return SqsTransportConfig(
        read_queue_url="http://localhost:4566/000000000000/high-throughput-read",
        max_messages=10,  # Process more messages per batch
        wait_time_seconds=1,
        poll_interval_seconds=0.001,  # Very fast polling
        visibility_timeout_seconds=10,
    )


@pytest.fixture
def mock_mcp_server():
    """Mock MCP server for session manager testing."""
    from unittest.mock import AsyncMock

    mock_server = MagicMock()
    mock_server.run = AsyncMock()
    mock_server.create_initialization_options = MagicMock(return_value={})
    return mock_server
