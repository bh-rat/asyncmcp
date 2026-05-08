"""Tests for SNS/SQS utility functions."""

import pytest
from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from asyncmcp.sns_sqs.utils import (
    SnsSqsClientConfig,
    SnsSqsServerConfig,
    extract_client_topic_arn_from_meta,
)


class TestSnsSqsServerConfig:
    """Test SnsSqsServerConfig dataclass."""

    def test_default_config(self):
        config = SnsSqsServerConfig(sqs_queue_url="http://localhost:4566/000000000000/server-queue")

        assert config.sqs_queue_url == "http://localhost:4566/000000000000/server-queue"
        assert config.max_messages == 10
        assert config.wait_time_seconds == 20
        assert config.poll_interval_seconds == 5.0

    def test_validation_requires_queue_url(self):
        with pytest.raises(ValueError, match="sqs_queue_url is required"):
            SnsSqsServerConfig(sqs_queue_url="")


class TestSnsSqsClientConfig:
    """Test SnsSqsClientConfig dataclass."""

    def test_validation_requires_queue_url(self):
        with pytest.raises(ValueError, match="sqs_queue_url is required"):
            SnsSqsClientConfig(sqs_queue_url="", sns_topic_arn="arn:aws:sns:us-east-1:000000000000:topic")

    def test_validation_requires_topic_arn(self):
        with pytest.raises(ValueError, match="sns_topic_arn is required"):
            SnsSqsClientConfig(sqs_queue_url="http://localhost:4566/000000000000/queue", sns_topic_arn="")


@pytest.fixture
def sample_jsonrpc_initialize_request():
    """Initialize request carrying clientTopicArn in _meta."""
    return JSONRPCMessage(
        root=JSONRPCRequest(
            jsonrpc="2.0",
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "_meta": {
                    "clientTopicArn": "arn:aws:sns:us-east-1:000000000000:client-topic",
                },
            },
        )
    )


class TestExtractClientTopicArnFromMeta:
    """Test extract_client_topic_arn_from_meta helper."""

    def test_valid(self, sample_jsonrpc_initialize_request):
        arn = extract_client_topic_arn_from_meta(sample_jsonrpc_initialize_request)

        assert arn == "arn:aws:sns:us-east-1:000000000000:client-topic"

    def test_missing_meta(self, sample_jsonrpc_request):
        arn = extract_client_topic_arn_from_meta(sample_jsonrpc_request)

        assert arn is None

    def test_meta_without_client_topic_arn(self):
        message = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={"_meta": {"someOtherKey": "value"}},
            )
        )

        assert extract_client_topic_arn_from_meta(message) is None

    def test_meta_is_not_dict(self):
        message = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={"_meta": "not-a-dict"},
            )
        )

        assert extract_client_topic_arn_from_meta(message) is None

    def test_no_params(self):
        message = JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/method"))

        assert extract_client_topic_arn_from_meta(message) is None

    def test_notification_returns_none(self, sample_jsonrpc_notification):
        arn = extract_client_topic_arn_from_meta(sample_jsonrpc_notification)

        assert arn is None

    def test_notification_with_meta_still_returns_none(self):
        message = JSONRPCMessage(
            root=JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/initialized",
                params={"_meta": {"clientTopicArn": "arn:aws:sns:us-east-1:000000000000:topic"}},
            )
        )

        assert extract_client_topic_arn_from_meta(message) is None

    def test_response_returns_none(self):
        message = JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"ok": True}))

        assert extract_client_topic_arn_from_meta(message) is None
