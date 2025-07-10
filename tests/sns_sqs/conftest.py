"""
Configuration and shared fixtures for SQS+SNS transport tests.
"""

# Import all shared fixtures so they're available to all test files
from tests.sns_sqs.shared_fixtures import (
    mock_sqs_client,
    mock_sns_client,
    sample_jsonrpc_request,
    sample_jsonrpc_response,
    sample_jsonrpc_notification,
    sample_sqs_message,
    sample_sns_wrapped_message,
    client_transport_config,
    server_transport_config,
    client_server_config,
)

# Make fixtures available for import
__all__ = [
    "mock_sqs_client",
    "mock_sns_client",
    "sample_jsonrpc_request",
    "sample_jsonrpc_response",
    "sample_jsonrpc_notification",
    "sample_sqs_message",
    "sample_sns_wrapped_message",
    "client_transport_config",
    "server_transport_config",
    "client_server_config",
]

import pytest
import anyio
from unittest.mock import MagicMock, AsyncMock
from typing import Dict, Any, List

# anyio backend configuration moved to top-level conftest.py


@pytest.fixture
def mock_aws_credentials():
    """Mock AWS credentials for testing."""
    return {
        "aws_access_key_id": "test-access-key",
        "aws_secret_access_key": "test-secret-key",
        "region_name": "us-east-1",
    }


@pytest.fixture
def mock_localstack_endpoints():
    """Mock LocalStack endpoints for testing."""
    return {"sqs": "http://localhost:4566", "sns": "http://localhost:4566"}


@pytest.fixture
def sample_queue_urls():
    """Sample SQS queue URLs for testing."""
    return {
        "client_queue": "http://localhost:4566/000000000000/client-responses",
        "server_queue": "http://localhost:4566/000000000000/server-requests",
    }


@pytest.fixture
def sample_topic_arns():
    """Sample SNS topic ARNs for testing."""
    return {
        "client_topic": "arn:aws:sns:us-east-1:000000000000:client-requests",
        "server_topic": "arn:aws:sns:us-east-1:000000000000:server-responses",
    }


@pytest.fixture
def mock_sqs_responses():
    """Mock SQS API responses for testing."""
    return {
        "empty_response": {"Messages": []},
        "single_message": {
            "Messages": [
                {
                    "MessageId": "test-msg-123",
                    "ReceiptHandle": "test-receipt-handle",
                    "Body": '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}',
                    "MessageAttributes": {},
                }
            ]
        },
        "multiple_messages": {
            "Messages": [
                {
                    "MessageId": f"test-msg-{i}",
                    "ReceiptHandle": f"test-receipt-handle-{i}",
                    "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "test", "params": {{}}}}',
                    "MessageAttributes": {},
                }
                for i in range(3)
            ]
        },
    }


@pytest.fixture
def mock_sns_responses():
    """Mock SNS API responses for testing."""
    return {"publish_success": {"MessageId": "sns-msg-123", "SequenceNumber": "1"}}


@pytest.fixture
def sample_jsonrpc_messages():
    """Sample JSON-RPC messages for testing."""
    return {
        "request": {"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {"key": "value"}},
        "response": {"jsonrpc": "2.0", "id": 1, "result": {"status": "success"}},
        "notification": {"jsonrpc": "2.0", "method": "test/notification", "params": {"event": "test"}},
        "error_response": {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid Request"}},
    }


@pytest.fixture
def sample_sns_wrapped_messages():
    """Sample SNS-wrapped messages for testing."""
    return {
        "notification": {
            "Type": "Notification",
            "MessageId": "sns-notification-123",
            "Message": '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}',
        },
        "subscription_confirmation": {
            "Type": "SubscriptionConfirmation",
            "MessageId": "sns-confirm-123",
            "Token": "test-token",
            "SubscribeURL": "https://sns.us-east-1.amazonaws.com/test",
        },
    }


@pytest.fixture
def timeout_config():
    """Timeout configuration for tests."""
    return {"short_timeout": 0.1, "medium_timeout": 0.5, "long_timeout": 2.0}


class MockSQSClient:
    """Mock SQS client for comprehensive testing."""

    def __init__(self):
        self.receive_message = MagicMock()
        self.delete_message = MagicMock()
        self.send_message = MagicMock()
        self.get_queue_url = MagicMock()
        self.create_queue = MagicMock()
        self.delete_queue = MagicMock()
        self.get_queue_attributes = MagicMock()

        # Default responses
        self.receive_message.return_value = {"Messages": []}
        self.delete_message.return_value = {}
        self.send_message.return_value = {"MessageId": "test-msg-123"}


class MockSNSClient:
    """Mock SNS client for comprehensive testing."""

    def __init__(self):
        self.publish = MagicMock()
        self.create_topic = MagicMock()
        self.delete_topic = MagicMock()
        self.subscribe = MagicMock()
        self.unsubscribe = MagicMock()
        self.get_topic_attributes = MagicMock()

        # Default responses
        self.publish.return_value = {"MessageId": "sns-msg-123"}
        self.create_topic.return_value = {"TopicArn": "arn:aws:sns:us-east-1:000000000000:test-topic"}


@pytest.fixture
def enhanced_mock_sqs_client():
    """Enhanced mock SQS client with more realistic behavior."""
    return MockSQSClient()


@pytest.fixture
def enhanced_mock_sns_client():
    """Enhanced mock SNS client with more realistic behavior."""
    return MockSNSClient()


@pytest.fixture
def message_factory():
    """Factory for creating test messages."""

    def create_sqs_message(
        message_id: str = "test-msg",
        receipt_handle: str = "test-handle",
        body: str = None,
        attributes: Dict[str, Any] = None,
    ):
        if body is None:
            body = '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}'

        return {
            "MessageId": message_id,
            "ReceiptHandle": receipt_handle,
            "Body": body,
            "MessageAttributes": attributes or {},
        }

    def create_sns_message(
        message_id: str = "sns-msg",
        topic_arn: str = "arn:aws:sns:us-east-1:000000000000:test",
        message: str = None,
        attributes: Dict[str, Any] = None,
    ):
        if message is None:
            message = '{"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}'

        return {
            "MessageId": message_id,
            "TopicArn": topic_arn,
            "Message": message,
            "MessageAttributes": attributes or {},
        }

    return {"sqs_message": create_sqs_message, "sns_message": create_sns_message}


@pytest.fixture
def error_scenarios():
    """Common error scenarios for testing."""
    return {
        "sqs_connection_error": Exception("Connection to SQS failed"),
        "sns_connection_error": Exception("Connection to SNS failed"),
        "invalid_json": '{"invalid": json}',
        "invalid_jsonrpc": '{"not": "jsonrpc"}',
        "permission_denied": Exception("Access denied"),
        "queue_not_found": Exception("Queue does not exist"),
        "topic_not_found": Exception("Topic does not exist"),
    }


@pytest.fixture
def performance_config():
    """Configuration for performance testing."""
    return {
        "message_counts": [1, 10, 50, 100],
        "concurrent_operations": [1, 5, 10],
        "batch_sizes": [1, 5, 10],
        "timeout_limits": [1.0, 5.0, 10.0],
    }


@pytest.fixture(autouse=True)
def cleanup_async_tasks():
    """Automatically cleanup async tasks after each test."""
    yield

    # Cancel any remaining tasks only if there's a running event loop
    try:
        # Use anyio equivalent if available
        import anyio.lowlevel
        try:
            anyio.lowlevel.current_task()
            # There's a running anyio context, but anyio doesn't have a direct equivalent
            # to asyncio.all_tasks(), so we'll skip cleanup in anyio context
        except RuntimeError:
            # No running anyio context
            pass
    except RuntimeError:
        # No running event loop, nothing to clean up
        pass


@pytest.fixture
def integration_test_config():
    """Configuration for integration tests."""
    return {
        "client_config": {
            "sqs_queue_url": "http://localhost:4566/000000000000/client-responses",
            "sns_topic_arn": "arn:aws:sns:us-east-1:000000000000:server-requests",
            "max_messages": 5,
            "wait_time_seconds": 1,
            "poll_interval_seconds": 0.1,
        },
        "server_config": {
            "sqs_queue_url": "http://localhost:4566/000000000000/server-requests",
            "sns_topic_arn": "arn:aws:sns:us-east-1:000000000000:client-responses",
            "max_messages": 5,
            "wait_time_seconds": 1,
            "poll_interval_seconds": 0.1,
        },
    }


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "performance: mark test as performance test")
    config.addinivalue_line("markers", "slow: mark test as slow running")


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names."""
    for item in items:
        # Add integration marker to integration tests
        if "integration" in item.nodeid:
            item.add_marker(pytest.mark.integration)

        # Add performance marker to performance tests
        if "performance" in item.nodeid or "load" in item.nodeid:
            item.add_marker(pytest.mark.performance)

        # Add slow marker to tests that might take longer
        if any(keyword in item.nodeid for keyword in ["high_load", "concurrent", "bulk"]):
            item.add_marker(pytest.mark.slow)
