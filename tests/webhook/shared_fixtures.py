"""
Shared fixtures for webhook transport tests.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from typing import Dict, Any

import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp.webhook.utils import WebhookTransportConfig


@pytest.fixture
def webhook_transport_config():
    """Create a webhook transport configuration for testing."""
    return WebhookTransportConfig(
        server_host="localhost",
        server_port=8000,
        webhook_host="localhost",
        webhook_port=8001,
        client_id="test-client",
        timeout_seconds=10.0,
    )


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for testing."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "success"}
    mock_client.post.return_value = mock_response
    mock_client.get.return_value = mock_response
    return mock_client


@pytest.fixture
def mock_uvicorn_server():
    """Mock uvicorn.Server for testing."""
    mock_server = AsyncMock()
    mock_server.serve = AsyncMock()
    mock_server.should_exit = False
    return mock_server


@pytest.fixture
def sample_jsonrpc_request():
    """Sample JSON-RPC request for testing."""
    return types.JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="test/method",
        params={"key": "value"}
    )


@pytest.fixture
def sample_jsonrpc_response():
    """Sample JSON-RPC response for testing."""
    return types.JSONRPCResponse(
        jsonrpc="2.0",
        id=1,
        result={"success": True}
    )


@pytest.fixture
def sample_jsonrpc_notification():
    """Sample JSON-RPC notification for testing."""
    return types.JSONRPCNotification(
        jsonrpc="2.0",
        method="test/notification",
        params={"data": "test"}
    )


@pytest.fixture
def sample_initialize_request():
    """Sample initialize request for testing."""
    return types.JSONRPCRequest(
        jsonrpc="2.0",
        id=1,
        method="initialize",
        params={
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
            "_meta": {"webhookUrl": "http://localhost:8001/webhook/response"}
        }
    )


@pytest.fixture
def sample_initialized_notification():
    """Sample initialized notification for testing."""
    return types.JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/initialized",
        params={}
    )


@pytest.fixture
def sample_session_message(sample_jsonrpc_request):
    """Sample session message for testing."""
    message = types.JSONRPCMessage(root=sample_jsonrpc_request)
    return SessionMessage(message)


@pytest.fixture
def sample_session_message_with_metadata(sample_jsonrpc_request):
    """Sample session message with metadata for testing."""
    message = types.JSONRPCMessage(root=sample_jsonrpc_request)
    return SessionMessage(
        message, 
        metadata={
            "session_id": "test-session-123",
            "client_id": "test-client",
            "webhook_url": "http://localhost:8001/webhook/response"
        }
    )


@pytest.fixture
def sample_http_request_body(sample_jsonrpc_request):
    """Sample HTTP request body for testing."""
    message = types.JSONRPCMessage(root=sample_jsonrpc_request)
    return message.model_dump_json(by_alias=True, exclude_none=True).encode('utf-8')


@pytest.fixture
def sample_http_headers():
    """Sample HTTP headers for testing."""
    return {
        "Content-Type": "application/json",
        "X-Client-ID": "test-client",
        "X-Request-ID": "1",
        "X-Method": "test/method",
        "X-Session-ID": "test-session-123",
    } 