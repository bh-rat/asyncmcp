"""
Tests for transport adapters.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.lowlevel import Server

from asyncmcp.multi_transport.adapters import AsyncMcpTransportAdapter, HttpTransportAdapter, WebhookTransportAdapter


@pytest.fixture
def mcp_server():
    """Create a mock MCP server."""
    return MagicMock(spec=Server)


class TestHttpTransportAdapter:
    """Test HttpTransportAdapter functionality."""

    def test_initialization_defaults(self, mcp_server):
        """Test HTTP adapter initialization with default values."""
        adapter = HttpTransportAdapter(mcp_server)

        assert adapter.mcp_server is mcp_server
        assert adapter.host == "localhost"
        assert adapter.port == 8000
        assert adapter.path == "/mcp"
        assert adapter.stateless is False
        assert adapter.json_response is False
        assert adapter.security_settings is None
        assert not adapter.is_running

    def test_initialization_custom_values(self, mcp_server):
        """Test HTTP adapter initialization with custom values."""
        adapter = HttpTransportAdapter(
            mcp_server=mcp_server, host="0.0.0.0", port=9000, path="/api/mcp", stateless=True, json_response=True
        )

        assert adapter.host == "0.0.0.0"
        assert adapter.port == 9000
        assert adapter.path == "/api/mcp"
        assert adapter.stateless is True
        assert adapter.json_response is True

    @pytest.mark.anyio
    async def test_start_http_adapter(self, mcp_server):
        """Test starting HTTP adapter."""
        adapter = HttpTransportAdapter(mcp_server, port=8080)

        with patch("asyncmcp.multi_transport.adapters.StreamableHTTPSessionManager") as mock_manager_class:
            with patch("asyncmcp.multi_transport.adapters.anyio.create_task_group") as mock_tg:
                with patch("asyncmcp.multi_transport.adapters.uvicorn.Server") as mock_uvicorn:
                    # Set up mocks
                    mock_manager = MagicMock()
                    mock_manager_class.return_value = mock_manager

                    mock_context = AsyncMock()
                    mock_manager.run.return_value = mock_context

                    mock_task_group = AsyncMock()
                    mock_tg.return_value = mock_task_group

                    mock_server = MagicMock()
                    mock_uvicorn.return_value = mock_server

                    await adapter.start()

                    # Verify session manager was created with correct parameters
                    mock_manager_class.assert_called_once_with(
                        app=mcp_server, stateless=False, json_response=False, security_settings=None
                    )

                    # Verify context manager was entered
                    mock_context.__aenter__.assert_called_once()

                    # Verify task group was created and entered
                    mock_tg.assert_called_once()
                    mock_task_group.__aenter__.assert_called_once()

                    # Verify uvicorn server was created with correct config
                    assert mock_uvicorn.called

                    # Verify adapter is running
                    assert adapter.is_running

    @pytest.mark.anyio
    async def test_start_already_running(self, mcp_server):
        """Test starting adapter that's already running."""
        adapter = HttpTransportAdapter(mcp_server)
        adapter._is_running = True

        await adapter.start()

        # Should remain running, no additional setup
        assert adapter.is_running

    @pytest.mark.anyio
    async def test_stop_http_adapter(self, mcp_server):
        """Test stopping HTTP adapter."""
        adapter = HttpTransportAdapter(mcp_server)

        # Set up mock state as if adapter was started
        mock_server = MagicMock()
        mock_context = AsyncMock()
        mock_task_group = AsyncMock()

        adapter._http_server = mock_server
        adapter._context_manager = mock_context
        adapter._server_task = mock_task_group
        adapter._is_running = True

        await adapter.stop()

        # Verify cleanup
        assert mock_server.should_exit is True
        mock_context.__aexit__.assert_called_once()
        mock_task_group.cancel_scope.cancel.assert_called_once()
        mock_task_group.__aexit__.assert_called_once()

        assert not adapter.is_running

    @pytest.mark.anyio
    async def test_stop_not_running(self, mcp_server):
        """Test stopping adapter that's not running."""
        adapter = HttpTransportAdapter(mcp_server)

        await adapter.stop()

        # Should be no-op
        assert not adapter.is_running

    @pytest.mark.anyio
    async def test_start_with_exception_cleanup(self, mcp_server):
        """Test that exceptions during start trigger cleanup."""
        adapter = HttpTransportAdapter(mcp_server)

        with patch(
            "asyncmcp.multi_transport.adapters.StreamableHTTPSessionManager", side_effect=Exception("Setup failed")
        ):
            with pytest.raises(Exception, match="Setup failed"):
                await adapter.start()

            # Should not be running after exception
            assert not adapter.is_running


class TestWebhookTransportAdapter:
    """Test WebhookTransportAdapter functionality."""

    def test_initialization(self):
        """Test webhook adapter initialization."""
        mock_manager = MagicMock()

        adapter = WebhookTransportAdapter(mock_manager, external_management=False)

        assert adapter.webhook_manager is mock_manager
        assert adapter.external_management is False
        assert not adapter.is_running

    def test_initialization_external_management(self):
        """Test webhook adapter with external management."""
        mock_manager = MagicMock()

        adapter = WebhookTransportAdapter(mock_manager, external_management=True)

        assert adapter.external_management is True

    @pytest.mark.anyio
    async def test_start_internal_management(self):
        """Test starting webhook adapter with internal management."""
        mock_manager = MagicMock()
        mock_context = AsyncMock()
        mock_manager.run.return_value = mock_context

        adapter = WebhookTransportAdapter(mock_manager, external_management=False)

        await adapter.start()

        # Verify context manager was entered
        mock_context.__aenter__.assert_called_once()
        assert adapter.is_running

    @pytest.mark.anyio
    async def test_start_external_management(self):
        """Test starting webhook adapter with external management."""
        mock_manager = MagicMock()

        adapter = WebhookTransportAdapter(mock_manager, external_management=True)

        await adapter.start()

        # Should not call manager.run() for external management
        mock_manager.run.assert_not_called()
        assert adapter.is_running

    @pytest.mark.anyio
    async def test_start_already_running(self):
        """Test starting webhook adapter that's already running."""
        mock_manager = MagicMock()
        adapter = WebhookTransportAdapter(mock_manager)
        adapter._is_running = True

        await adapter.start()

        # Should not try to start again
        mock_manager.run.assert_not_called()

    @pytest.mark.anyio
    async def test_stop_internal_management(self):
        """Test stopping webhook adapter with internal management."""
        mock_manager = MagicMock()
        mock_context = AsyncMock()

        adapter = WebhookTransportAdapter(mock_manager, external_management=False)
        adapter._context_manager = mock_context
        adapter._is_running = True

        await adapter.stop()

        # Verify context manager was exited
        mock_context.__aexit__.assert_called_once()
        assert not adapter.is_running

    @pytest.mark.anyio
    async def test_stop_external_management(self):
        """Test stopping webhook adapter with external management."""
        mock_manager = MagicMock()

        adapter = WebhookTransportAdapter(mock_manager, external_management=True)
        adapter._is_running = True

        await adapter.stop()

        # Should not call context manager methods
        assert not adapter.is_running

    @pytest.mark.anyio
    async def test_stop_not_running(self):
        """Test stopping webhook adapter that's not running."""
        mock_manager = MagicMock()
        adapter = WebhookTransportAdapter(mock_manager)

        await adapter.stop()

        # Should be no-op
        assert not adapter.is_running

    @pytest.mark.anyio
    async def test_start_with_exception(self):
        """Test webhook adapter start with exception."""
        mock_manager = MagicMock()
        mock_context = AsyncMock()
        mock_context.__aenter__.side_effect = Exception("Start failed")
        mock_manager.run.return_value = mock_context

        adapter = WebhookTransportAdapter(mock_manager, external_management=False)

        with pytest.raises(Exception, match="Start failed"):
            await adapter.start()

        assert not adapter.is_running


class TestAsyncMcpTransportAdapter:
    """Test AsyncMcpTransportAdapter functionality."""

    def test_initialization(self):
        """Test AsyncMCP transport adapter initialization."""
        mock_transport = MagicMock()
        session_id = "test-session-123"

        adapter = AsyncMcpTransportAdapter(mock_transport, session_id)

        assert adapter.transport_instance is mock_transport
        assert adapter.session_id == session_id
        assert not adapter.is_terminated

    def test_initialization_no_session_id(self):
        """Test adapter initialization without session ID."""
        mock_transport = MagicMock()

        adapter = AsyncMcpTransportAdapter(mock_transport)

        assert adapter.session_id is None

    @pytest.mark.anyio
    async def test_connect_success(self):
        """Test successful connection through adapter."""
        mock_transport = MagicMock()
        mock_streams = ("receive_stream", "send_stream")

        # Set up async context manager
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_streams)
        mock_transport.connect.return_value = mock_context

        adapter = AsyncMcpTransportAdapter(mock_transport)

        async with adapter.connect() as streams:
            assert streams == mock_streams

        mock_transport.connect.assert_called_once()
        mock_context.__aenter__.assert_called_once()
        mock_context.__aexit__.assert_called_once()

    @pytest.mark.anyio
    async def test_connect_no_connect_method(self):
        """Test connect when transport doesn't have connect method."""
        mock_transport = MagicMock()
        del mock_transport.connect

        adapter = AsyncMcpTransportAdapter(mock_transport)

        with pytest.raises(NotImplementedError, match="does not support connect"):
            async with adapter.connect():
                pass

    @pytest.mark.anyio
    async def test_terminate_with_terminate_method(self):
        """Test termination when transport has terminate method."""
        mock_transport = AsyncMock()

        adapter = AsyncMcpTransportAdapter(mock_transport)

        await adapter.terminate()

        assert adapter.is_terminated
        mock_transport.terminate.assert_called_once()

    @pytest.mark.anyio
    async def test_terminate_with_cleanup_method(self):
        """Test termination when transport has cleanup method but no terminate."""
        mock_transport = AsyncMock()
        del mock_transport.terminate

        adapter = AsyncMcpTransportAdapter(mock_transport)

        await adapter.terminate()

        assert adapter.is_terminated
        mock_transport.cleanup.assert_called_once()

    @pytest.mark.anyio
    async def test_terminate_already_terminated(self):
        """Test termination when already terminated."""
        mock_transport = AsyncMock()

        adapter = AsyncMcpTransportAdapter(mock_transport)
        adapter._terminated = True

        await adapter.terminate()

        # Should not call transport methods
        mock_transport.terminate.assert_not_called()

    @pytest.mark.anyio
    async def test_cleanup_method(self):
        """Test cleanup method."""
        mock_transport = AsyncMock()

        adapter = AsyncMcpTransportAdapter(mock_transport)

        await adapter.cleanup()

        mock_transport.cleanup.assert_called_once()

    @pytest.mark.anyio
    async def test_cleanup_no_method(self):
        """Test cleanup when transport doesn't have cleanup method."""
        mock_transport = MagicMock()
        del mock_transport.cleanup

        adapter = AsyncMcpTransportAdapter(mock_transport)

        # Should not raise exception
        await adapter.cleanup()


class TestAdapterIntegration:
    """Test adapter integration scenarios."""

    @pytest.mark.anyio
    async def test_http_adapter_full_lifecycle(self, mcp_server):
        """Test complete HTTP adapter lifecycle."""
        adapter = HttpTransportAdapter(mcp_server, port=8085)

        with patch("asyncmcp.multi_transport.adapters.StreamableHTTPSessionManager") as mock_manager_class:
            with patch("asyncmcp.multi_transport.adapters.anyio.create_task_group") as mock_tg:
                with patch("asyncmcp.multi_transport.adapters.uvicorn.Server") as mock_uvicorn:
                    # Set up mocks
                    mock_manager = MagicMock()
                    mock_manager_class.return_value = mock_manager

                    mock_context = AsyncMock()
                    mock_manager.run.return_value = mock_context

                    mock_task_group = AsyncMock()
                    mock_tg.return_value = mock_task_group

                    # Start adapter
                    await adapter.start()
                    assert adapter.is_running

                    # Stop adapter
                    await adapter.stop()
                    assert not adapter.is_running

    @pytest.mark.anyio
    async def test_webhook_adapter_full_lifecycle(self):
        """Test complete webhook adapter lifecycle."""
        mock_manager = MagicMock()
        mock_context = AsyncMock()
        mock_manager.run.return_value = mock_context

        adapter = WebhookTransportAdapter(mock_manager, external_management=False)

        # Start adapter
        await adapter.start()
        assert adapter.is_running
        mock_context.__aenter__.assert_called_once()

        # Stop adapter
        await adapter.stop()
        assert not adapter.is_running
        mock_context.__aexit__.assert_called_once()

    @pytest.mark.anyio
    async def test_adapter_error_handling(self, mcp_server):
        """Test adapter error handling scenarios."""
        adapter = HttpTransportAdapter(mcp_server)

        # Test start failure
        with patch(
            "asyncmcp.multi_transport.adapters.StreamableHTTPSessionManager",
            side_effect=RuntimeError("Manager creation failed"),
        ):
            with pytest.raises(RuntimeError):
                await adapter.start()

            assert not adapter.is_running

        # Test stop with partial state
        adapter._is_running = True
        adapter._http_server = None  # Partial state

        await adapter.stop()  # Should handle gracefully
        assert not adapter.is_running


class TestAdapterConfiguration:
    """Test adapter configuration scenarios."""

    def test_http_adapter_configuration_validation(self, mcp_server):
        """Test HTTP adapter configuration validation."""
        # Test with various configurations
        configs = [
            {"host": "127.0.0.1", "port": 3000, "path": "/custom"},
            {"stateless": True, "json_response": True},
            {"port": 0},  # System-assigned port
        ]

        for config in configs:
            adapter = HttpTransportAdapter(mcp_server, **config)

            for key, value in config.items():
                assert getattr(adapter, key) == value

    def test_webhook_adapter_configuration(self):
        """Test webhook adapter configuration options."""
        mock_manager = MagicMock()

        # Test internal management
        adapter1 = WebhookTransportAdapter(mock_manager, external_management=False)
        assert not adapter1.external_management

        # Test external management
        adapter2 = WebhookTransportAdapter(mock_manager, external_management=True)
        assert adapter2.external_management

    def test_asyncmcp_adapter_configuration(self):
        """Test AsyncMCP adapter configuration."""
        mock_transport = MagicMock()

        # Test with session ID
        adapter1 = AsyncMcpTransportAdapter(mock_transport, "session-123")
        assert adapter1.session_id == "session-123"

        # Test without session ID
        adapter2 = AsyncMcpTransportAdapter(mock_transport)
        assert adapter2.session_id is None
