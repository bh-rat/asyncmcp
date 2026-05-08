"""Tests for SQS utility functions."""

from mcp.types import JSONRPCMessage, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse

from asyncmcp.sqs.utils import (
    SqsClientConfig,
    SqsServerConfig,
    extract_response_queue_url_from_meta,
)


class TestSqsServerConfig:
    """Test SqsServerConfig dataclass."""

    def test_default_config(self):
        config = SqsServerConfig(read_queue_url="http://localhost:4566/000000000000/server-requests")

        assert config.read_queue_url == "http://localhost:4566/000000000000/server-requests"
        assert config.max_messages == 10
        assert config.wait_time_seconds == 20
        assert config.poll_interval_seconds == 5.0
        assert config.transport_timeout_seconds is None


class TestSqsClientConfig:
    """Test SqsClientConfig dataclass."""

    def test_client_id_auto_generated_when_omitted(self):
        config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
        )

        assert config.client_id is not None
        assert isinstance(config.client_id, str)

    def test_client_id_preserved_when_provided(self):
        config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
            client_id="explicit-client",
        )

        assert config.client_id == "explicit-client"


class TestExtractResponseQueueUrlFromMeta:
    """Test extract_response_queue_url_from_meta helper."""

    def test_valid(self, sample_jsonrpc_initialize_request):
        url = extract_response_queue_url_from_meta(sample_jsonrpc_initialize_request)

        assert url == "http://localhost:4566/000000000000/client-responses"

    def test_missing_meta(self, sample_jsonrpc_request):
        url = extract_response_queue_url_from_meta(sample_jsonrpc_request)

        assert url is None

    def test_meta_without_response_queue_url(self):
        message = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={"_meta": {"someOtherKey": "value"}},
            )
        )

        assert extract_response_queue_url_from_meta(message) is None

    def test_meta_is_not_dict(self):
        message = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={"_meta": "not-a-dict"},
            )
        )

        assert extract_response_queue_url_from_meta(message) is None

    def test_no_params(self):
        message = JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/method"))

        assert extract_response_queue_url_from_meta(message) is None

    def test_notification_returns_none(self, sample_jsonrpc_notification):
        url = extract_response_queue_url_from_meta(sample_jsonrpc_notification)

        assert url is None

    def test_notification_with_meta_still_returns_none(self):
        message = JSONRPCMessage(
            root=JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/initialized",
                params={"_meta": {"responseQueueUrl": "http://example.com/queue"}},
            )
        )

        assert extract_response_queue_url_from_meta(message) is None

    def test_response_returns_none(self):
        message = JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"ok": True}))

        assert extract_response_queue_url_from_meta(message) is None
