"""Integration tests for proxy functionality."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from asyncmcp.proxy import ProxyConfig, ProxySessionManager
from asyncmcp.proxy.client import ProxyClient
from asyncmcp.sqs.utils import SqsClientConfig


class TestProxyIntegration:
    """Integration tests for proxy server functionality."""

    @pytest.fixture
    def mock_sqs_client(self):
        """Create a mock SQS client."""
        return MagicMock()

    @pytest.fixture
    def proxy_config(self, mock_sqs_client):
        """Create proxy configuration."""
        backend_config = SqsClientConfig(
            read_queue_url="https://sqs.example.com/queue",
            response_queue_url="https://sqs.example.com/response",
        )

        return ProxyConfig(
            backend_transport="sqs",
            backend_config=backend_config,
            backend_clients={"sqs_client": mock_sqs_client},
            host="127.0.0.1",
            port=8080,
            max_sessions=10,
        )

    @pytest.mark.anyio
    async def test_session_creation_and_message_flow(self, proxy_config):
        """Test basic session creation and message flow."""
        # Create session manager
        manager = ProxySessionManager(proxy_config)

        # Start manager
        await manager.start()

        try:
            # Create a session
            session_id = manager.create_session_id()
            assert not manager.has_session(session_id)

            # Get response stream creates session
            response_stream = await manager.get_response_stream(session_id)
            assert manager.has_session(session_id)
            assert manager.active_session_count == 1

            # Test session ID management
            assert manager.get_single_session_id() == session_id

        finally:
            await manager.stop()
            assert manager.active_session_count == 0

    @pytest.mark.anyio
    async def test_message_routing(self, proxy_config):
        """Test message routing through proxy."""
        manager = ProxySessionManager(proxy_config)
        await manager.start()

        try:
            # Create session
            session_id = manager.create_session_id()
            response_stream = await manager.get_response_stream(session_id)

            # Mock backend client behavior
            mock_client = ProxyClient(
                transport_type="sqs",
                config=proxy_config.backend_config,
                low_level_clients=proxy_config.backend_clients,
                session_id=session_id,
            )

            # Test sending message (would fail in real scenario without proper mocking)
            message_data = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

            # This would normally send to backend
            # For now just verify session exists
            assert manager.has_session(session_id)

        finally:
            await manager.stop()

    @pytest.mark.anyio
    async def test_multiple_sessions(self, proxy_config):
        """Test handling multiple sessions."""
        manager = ProxySessionManager(proxy_config)
        await manager.start()

        try:
            # Create multiple sessions
            session_ids = []
            for i in range(3):
                session_id = manager.create_session_id()
                await manager.get_response_stream(session_id)
                session_ids.append(session_id)

            assert manager.active_session_count == 3

            # With multiple sessions, get_single_session_id returns None
            assert manager.get_single_session_id() is None

            # Remove a session
            await manager.remove_session(session_ids[0])
            assert manager.active_session_count == 2
            assert not manager.has_session(session_ids[0])

        finally:
            await manager.stop()

    @pytest.mark.anyio
    async def test_session_limit(self, proxy_config):
        """Test session limit enforcement."""
        proxy_config.max_sessions = 2
        manager = ProxySessionManager(proxy_config)
        await manager.start()

        try:
            # Create max sessions
            session1 = manager.create_session_id()
            await manager.get_response_stream(session1)

            session2 = manager.create_session_id()
            await manager.get_response_stream(session2)

            assert manager.active_session_count == 2

            # Try to create one more
            session3 = manager.create_session_id()
            with pytest.raises(RuntimeError, match="Maximum session limit"):
                await manager.get_response_stream(session3)

        finally:
            await manager.stop()

    @pytest.mark.anyio
    async def test_wait_for_response(self, proxy_config):
        """Test synchronous response waiting."""
        manager = ProxySessionManager(proxy_config)
        await manager.start()

        try:
            session_id = manager.create_session_id()
            await manager.get_response_stream(session_id)

            # Start waiting for response
            request_id = 123
            wait_task = asyncio.create_task(manager.wait_for_response(session_id, request_id, timeout=1.0))

            # Give the task a moment to register the pending response
            await asyncio.sleep(0.1)

            # Simulate response
            message = SessionMessage(
                JSONRPCMessage.model_validate(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"tools": []},
                    }
                )
            )

            # Handle response
            should_forward = manager._handle_response(session_id, message)
            assert not should_forward  # Should be handled synchronously

            # Wait should complete
            response = await wait_task
            assert response is not None
            assert json.loads(response)["id"] == request_id

        finally:
            await manager.stop()
