"""
Shared fixtures for webhook transport tests.
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification
from mcp.server.lowlevel import Server

from asyncmcp.webhook.utils import WebhookTransportConfig


@pytest.fixture
def mock_http_client():
    """Mock HTTP client for testing."""
    client = MagicMock()
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_jsonrpc_request():
    """Sample JSON-RPC request message."""
    return JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/method", params={"key": "value"}))


@pytest.fixture
def sample_jsonrpc_initialize_request():
    """Sample JSON-RPC initialize request."""
    return JSONRPCMessage(
        root=JSONRPCRequest(
            jsonrpc="2.0",
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "_meta": {"webhookUrl": "http://localhost:8001/webhook/response"},
            },
        )
    )


@pytest.fixture
def sample_jsonrpc_response():
    """Sample JSON-RPC response message."""
    return JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"status": "success", "data": "test data"}))


@pytest.fixture
def sample_jsonrpc_notification():
    """Sample JSON-RPC notification message."""
    return JSONRPCMessage(root=JSONRPCNotification(jsonrpc="2.0", method="test/notification", params={"event": "test"}))


@pytest.fixture
def sample_webhook_request_body():
    """Sample webhook request body."""
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {"key": "value"}}).encode("utf-8")


@pytest.fixture
def sample_initialize_webhook_request():
    """Sample initialize webhook request body."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "_meta": {"webhookUrl": "http://localhost:8001/webhook/response"},
            },
        }
    ).encode("utf-8")


@pytest.fixture
def client_transport_config():
    """Test configuration for webhook client transport."""
    return WebhookTransportConfig(
        server_url="http://localhost:8000/mcp/request",
        webhook_url="http://localhost:8001/webhook/response",
        client_id="test-client",
        timeout_seconds=10.0,
    )


@pytest.fixture
def server_transport_config():
    """Test configuration for webhook server transport."""
    return WebhookTransportConfig(
        server_url="http://localhost:8000/mcp/request",
        webhook_url="http://localhost:8001/webhook/response",
        timeout_seconds=10.0,
    )


@pytest.fixture
def client_server_config():
    """Create client and server configurations for integration testing."""
    mock_client_http = MagicMock()
    mock_client_http.post = AsyncMock()
    mock_client_http.aclose = AsyncMock()

    mock_server_http = MagicMock()
    mock_server_http.post = AsyncMock()
    mock_server_http.aclose = AsyncMock()

    client_config = WebhookTransportConfig(
        server_url="http://localhost:8000/mcp/request",
        webhook_url="http://localhost:8001/webhook/response",
        client_id="test-client",
        timeout_seconds=5.0,
    )

    server_config = WebhookTransportConfig(
        server_url="http://localhost:8000/mcp/request",
        webhook_url="http://localhost:8001/webhook/response",
        timeout_seconds=5.0,
    )

    return {
        "client": {
            "config": client_config,
            "http_client": mock_client_http,
        },
        "server": {
            "config": server_config,
            "http_client": mock_server_http,
        },
    }


@pytest.fixture
def mock_mcp_server():
    """Mock MCP server for testing."""
    server = MagicMock(spec=Server)
    server.run = AsyncMock()
    server.create_initialization_options = MagicMock(return_value={})
    return server


@pytest.fixture
def bulk_webhook_requests():
    """Sample bulk webhook requests for high-throughput testing."""
    return [
        json.dumps({"jsonrpc": "2.0", "id": i, "method": "bulk/test", "params": {"index": i}}).encode("utf-8")
        for i in range(10)
    ]
