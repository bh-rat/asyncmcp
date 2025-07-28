"""
Integration tests for the complete multi-transport system.
"""

from unittest.mock import MagicMock, patch

import pytest
from mcp.server.lowlevel import Server

from asyncmcp.multi_transport import HttpTransportAdapter, MultiTransportServer, TransportType, transport_info


class MockTransportManager:
    """Mock transport manager for integration testing."""

    def __init__(self, transport_type: str, is_running: bool = False):
        self.transport_type = transport_type
        self._is_running = is_running
        self.start_called = False
        self.stop_called = False
        self.messages_sent = []

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        self.start_called = True
        self._is_running = True

    async def stop(self) -> None:
        self.stop_called = True
        self._is_running = False

    async def send_message(self, message):
        """Mock method for sending messages."""
        self.messages_sent.append(message)


@pytest.fixture
def mcp_server():
    """Create a mock MCP server with tool handlers."""
    server = MagicMock(spec=Server)

    # Define tool handlers with decorators
    @transport_info(TransportType.HTTP, description="Fast sync tool")
    def sync_tool():
        return "sync result"

    @transport_info(TransportType.WEBHOOK, description="Long async tool")
    def async_tool():
        return "async result"

    def default_tool():
        return "default result"

    server._tool_handlers = {"sync_tool": sync_tool, "async_tool": async_tool, "default_tool": default_tool}

    return server


@pytest.fixture
def multi_server(mcp_server):
    """Create a MultiTransportServer with mock MCP server."""
    return MultiTransportServer(mcp_server, enable_routing_validation=False)


class TestCompleteMultiTransportFlow:
    """Test the complete multi-transport workflow."""

    @pytest.mark.anyio
    async def test_full_server_lifecycle_with_multiple_transports(self, multi_server):
        """Test complete server lifecycle with multiple transports."""
        # Set up mock transports
        http_transport = MockTransportManager("http")
        webhook_transport = MockTransportManager("webhook")

        # Register transports
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP,
            transport_instance=http_transport,
            config={"host": "localhost", "port": 8000},
            tools=["sync_tool"],
            is_default=True,
        )

        multi_server.registry.register_transport(
            transport_type=TransportType.WEBHOOK,
            transport_instance=webhook_transport,
            config={"manager": webhook_transport},
            tools=["async_tool"],
        )

        # Test server lifecycle
        async with multi_server.run() as server:
            assert server is multi_server
            assert multi_server.is_running

            # Check that transports were started
            assert http_transport.start_called
            assert webhook_transport.start_called
            assert http_transport.is_running
            assert webhook_transport.is_running

            # Check transport registry state
            active_transports = multi_server.registry.get_active_transports()
            assert len(active_transports) == 2
            assert TransportType.HTTP in active_transports
            assert TransportType.WEBHOOK in active_transports

            # Test tool routing
            http_route = await multi_server.route_tool_call("sync_tool", {})
            webhook_route = await multi_server.route_tool_call("async_tool", {})
            default_route = await multi_server.route_tool_call("default_tool", {})

            assert http_route == TransportType.HTTP
            assert webhook_route == TransportType.WEBHOOK
            assert default_route == TransportType.HTTP  # Default transport

        # After server stops, transports should be stopped
        assert http_transport.stop_called
        assert webhook_transport.stop_called
        assert not multi_server.is_running

    def test_tool_discovery_and_metadata_registration(self, multi_server):
        """Test that tool discovery properly registers metadata from decorators."""
        # Trigger tool discovery
        multi_server._discover_tools()

        # Check that tools were discovered
        assert len(multi_server._registered_tools) == 3
        assert "sync_tool" in multi_server._registered_tools
        assert "async_tool" in multi_server._registered_tools
        assert "default_tool" in multi_server._registered_tools

        # Check that metadata was registered from decorators
        sync_metadata = multi_server.registry.get_tool_metadata("sync_tool")
        assert sync_metadata is not None
        assert sync_metadata.transport_type == TransportType.HTTP
        assert sync_metadata.description == "Fast sync tool"

        async_metadata = multi_server.registry.get_tool_metadata("async_tool")
        assert async_metadata is not None
        assert async_metadata.transport_type == TransportType.WEBHOOK
        assert async_metadata.description == "Long async tool"

        # default_tool should not have metadata (no decorator)
        default_metadata = multi_server.registry.get_tool_metadata("default_tool")
        assert default_metadata is None

    def test_routing_decisions_with_preferences_and_fallbacks(self, multi_server):
        """Test routing decisions with various scenarios."""
        # Set up transports
        http_transport = MockTransportManager("http")
        webhook_transport = MockTransportManager("webhook")

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=http_transport, config={}, is_default=True
        )
        multi_server.registry.register_transport(
            transport_type=TransportType.WEBHOOK, transport_instance=webhook_transport, config={}
        )

        # Make transports active
        multi_server.registry.get_transport_info(TransportType.HTTP).is_active = True
        multi_server.registry.get_transport_info(TransportType.WEBHOOK).is_active = True

        # Discover tools to register metadata
        multi_server._discover_tools()

        # Test routing decisions

        # Tool with explicit transport preference (available)
        decision1 = multi_server.registry.make_routing_decision(
            "sync_tool", [TransportType.HTTP, TransportType.WEBHOOK]
        )
        assert decision1 == TransportType.HTTP

        # Tool with explicit transport preference (available)
        decision2 = multi_server.registry.make_routing_decision(
            "async_tool", [TransportType.HTTP, TransportType.WEBHOOK]
        )
        assert decision2 == TransportType.WEBHOOK

        # Tool with explicit preference but transport not available (fallback to default)
        decision3 = multi_server.registry.make_routing_decision(
            "async_tool",
            [TransportType.HTTP],  # Only HTTP available
        )
        assert decision3 == TransportType.HTTP

        # Tool without explicit preference (uses default)
        decision4 = multi_server.registry.make_routing_decision(
            "default_tool", [TransportType.HTTP, TransportType.WEBHOOK]
        )
        assert decision4 == TransportType.HTTP

    def test_configuration_validation_comprehensive(self, multi_server):
        """Test comprehensive configuration validation."""
        # Set up transports and tools
        http_transport = MockTransportManager("http")
        webhook_transport = MockTransportManager("webhook")

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP,
            transport_instance=http_transport,
            config={},
            tools=["sync_tool"],
            is_default=True,
        )

        multi_server.registry.register_transport(
            transport_type=TransportType.WEBHOOK, transport_instance=webhook_transport, config={}, tools=["async_tool"]
        )

        # Make transports active
        multi_server.registry.get_transport_info(TransportType.HTTP).is_active = True
        multi_server.registry.get_transport_info(TransportType.WEBHOOK).is_active = True

        # Discover tools
        multi_server._discover_tools()

        # Validate configuration
        validation = multi_server.validate_configuration()

        assert validation["valid"] is True
        assert len(validation["errors"]) == 0
        assert validation["total_tools"] == 3
        assert validation["total_transports"] == 2
        assert validation["default_transport"] == "http"

        # Check tool routing assignments
        tool_routing = validation["tool_routing"]
        assert tool_routing["sync_tool"] == TransportType.HTTP
        assert tool_routing["async_tool"] == TransportType.WEBHOOK
        assert tool_routing["default_tool"] == TransportType.HTTP

    def test_tool_routing_info_comprehensive(self, multi_server):
        """Test comprehensive tool routing information."""
        # Set up system
        http_transport = MockTransportManager("http")
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=http_transport, config={}, is_default=True
        )

        multi_server._discover_tools()

        # Get routing info
        routing_info = multi_server.get_tool_routing_info()

        assert len(routing_info) == 3

        # Check sync_tool (has explicit transport)
        sync_info = routing_info["sync_tool"]
        assert sync_info["assigned_transport"] == "http"
        assert sync_info["has_explicit_transport"] is True
        assert sync_info["explicit_transport"] == "http"
        assert sync_info["description"] == "Fast sync tool"

        # Check async_tool (has explicit transport but not available)
        async_info = routing_info["async_tool"]
        assert async_info["assigned_transport"] == "http"  # Fallback
        assert async_info["has_explicit_transport"] is True
        assert async_info["explicit_transport"] == "webhook"
        assert async_info["description"] == "Long async tool"

        # Check default_tool (no explicit transport)
        default_info = routing_info["default_tool"]
        assert default_info["assigned_transport"] == "http"
        assert default_info["has_explicit_transport"] is False
        assert default_info["explicit_transport"] is None
        assert default_info["description"] is None

    def test_tool_routing_map_generation(self, multi_server):
        """Test tool routing map generation."""
        # Set up system
        http_transport = MockTransportManager("http")
        webhook_transport = MockTransportManager("webhook")

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=http_transport, config={}, is_default=True
        )
        multi_server.registry.register_transport(
            transport_type=TransportType.WEBHOOK, transport_instance=webhook_transport, config={}
        )

        multi_server._discover_tools()

        # Get routing map
        routing_map = multi_server.get_tool_routing_map()

        expected_map = {"sync_tool": "http", "async_tool": "webhook", "default_tool": "http"}

        assert routing_map == expected_map

    @pytest.mark.anyio
    async def test_error_handling_during_startup(self, multi_server):
        """Test error handling during server startup."""
        # Set up transport that fails to start
        failing_transport = MockTransportManager("http")

        async def failing_start():
            raise RuntimeError("Transport start failed")

        failing_transport.start = failing_start

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=failing_transport, config={}
        )

        # Server should start successfully even if transport fails
        # (start_all_transports continues on errors)
        async with multi_server.run():
            assert multi_server.is_running
            # Transport should not be active due to startup failure
            transport_info = multi_server.registry.get_transport_info(TransportType.HTTP)
            assert not transport_info.is_active

        # Server should not be running after context exit
        assert not multi_server.is_running

    @pytest.mark.anyio
    async def test_graceful_shutdown_with_errors(self, multi_server):
        """Test graceful shutdown even when transport stop fails."""
        # Set up transport that fails to stop
        problematic_transport = MockTransportManager("http")

        async def failing_stop():
            raise RuntimeError("Transport stop failed")

        problematic_transport.stop = failing_stop

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=problematic_transport, config={}
        )

        # Server should handle stop errors gracefully
        async with multi_server.run():
            assert multi_server.is_running

        # Server should be stopped despite transport stop error
        assert not multi_server.is_running

    def test_no_global_state_isolation(self):
        """Test that multiple servers don't interfere with each other."""
        # Create two separate servers
        server1 = MagicMock(spec=Server)
        server2 = MagicMock(spec=Server)

        @transport_info(TransportType.HTTP, description="Server 1 tool")
        def server1_tool():
            pass

        @transport_info(TransportType.WEBHOOK, description="Server 2 tool")
        def server2_tool():
            pass

        server1._tool_handlers = {"server1_tool": server1_tool}
        server2._tool_handlers = {"server2_tool": server2_tool}

        multi1 = MultiTransportServer(server1)
        multi2 = MultiTransportServer(server2)

        # Discover tools for both servers
        multi1._discover_tools()
        multi2._discover_tools()

        # Check that each server has its own metadata
        metadata1 = multi1.registry.get_tool_metadata("server1_tool")
        metadata2 = multi2.registry.get_tool_metadata("server2_tool")

        assert metadata1 is not None
        assert metadata1.transport_type == TransportType.HTTP

        assert metadata2 is not None
        assert metadata2.transport_type == TransportType.WEBHOOK

        # Each server should only know about its own tools
        assert multi1.registry.get_tool_metadata("server2_tool") is None
        assert multi2.registry.get_tool_metadata("server1_tool") is None

        # Registries should be independent
        assert multi1.registry is not multi2.registry

    @pytest.mark.anyio
    async def test_dynamic_transport_management(self, multi_server):
        """Test dynamic addition and removal of transports."""
        # Start with one transport
        http_transport = MockTransportManager("http")
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=http_transport, config={}, is_default=True
        )

        async with multi_server.run():
            # Initially only HTTP transport
            assert len(multi_server.registry.get_active_transports()) == 1

            # Add webhook transport dynamically
            webhook_transport = MockTransportManager("webhook")
            multi_server.registry.register_transport(
                transport_type=TransportType.WEBHOOK, transport_instance=webhook_transport, config={}
            )

            # Start the new transport
            await multi_server.registry.start_transport(TransportType.WEBHOOK)

            # Now should have both transports active
            assert len(multi_server.registry.get_active_transports()) == 2

            # Remove webhook transport
            await multi_server.registry.stop_transport(TransportType.WEBHOOK)
            multi_server.registry.unregister_transport(TransportType.WEBHOOK)

            # Back to one transport
            assert len(multi_server.registry.get_all_transports()) == 1

    def test_transport_tool_association_changes(self, multi_server):
        """Test changing tool associations for transports."""
        # Set up transport
        http_transport = MockTransportManager("http")
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=http_transport, config={}, tools=["tool1", "tool2"]
        )

        # Check initial tool mapping
        tool_mapping = multi_server.registry.get_tool_transport_mapping()
        assert tool_mapping["tool1"] == TransportType.HTTP
        assert tool_mapping["tool2"] == TransportType.HTTP

        # Update tools for transport
        multi_server.registry.set_transport_tools(TransportType.HTTP, ["tool2", "tool3"])

        # Check updated mapping
        updated_mapping = multi_server.registry.get_tool_transport_mapping()
        assert "tool1" not in updated_mapping
        assert updated_mapping["tool2"] == TransportType.HTTP
        assert updated_mapping["tool3"] == TransportType.HTTP


class TestRealWorldScenarios:
    """Test scenarios that mimic real-world usage."""

    @pytest.mark.anyio
    async def test_mixed_transport_server_setup(self):
        """Test a realistic mixed transport server setup."""
        # Create server with realistic tool handlers
        mcp_server = MagicMock(spec=Server)

        @transport_info(TransportType.HTTP, description="Fast data fetcher")
        def fetch_data():
            return {"data": "fetched"}

        @transport_info(TransportType.WEBHOOK, description="Long processing task")
        def process_data():
            return {"result": "processed"}

        def get_status():  # No transport preference
            return {"status": "ok"}

        mcp_server._tool_handlers = {"fetch_data": fetch_data, "process_data": process_data, "get_status": get_status}

        # Create multi-transport server
        server = MultiTransportServer(mcp_server, enable_routing_validation=True)

        # Add HTTP transport for fast operations
        with patch.object(HttpTransportAdapter, "__init__", return_value=None):
            with patch.object(HttpTransportAdapter, "start") as mock_start:
                with patch.object(HttpTransportAdapter, "stop") as mock_stop:
                    mock_adapter = MagicMock()
                    mock_adapter.start = mock_start
                    mock_adapter.stop = mock_stop

                    server.add_http_transport(
                        host="0.0.0.0", port=8000, tools=["fetch_data", "get_status"], is_default=True
                    )

        # Add webhook transport for long operations
        mock_webhook_manager = MagicMock()
        server.add_webhook_transport(
            webhook_manager=mock_webhook_manager, tools=["process_data"], external_management=True
        )

        # Discover tools first
        server._discover_tools()

        # Make transports active for validation
        server.registry.get_transport_info(TransportType.HTTP).is_active = True
        server.registry.get_transport_info(TransportType.WEBHOOK).is_active = True

        # Validate configuration
        validation = server.validate_configuration()

        assert validation["valid"] is True
        assert validation["total_tools"] == 3
        assert validation["total_transports"] == 2

        # Check routing assignments
        routing_map = server.get_tool_routing_map()
        assert routing_map["fetch_data"] == "http"
        assert routing_map["process_data"] == "webhook"
        assert routing_map["get_status"] == "http"  # Default transport

    def test_configuration_edge_cases(self):
        """Test configuration edge cases and error conditions."""
        mcp_server = MagicMock(spec=Server)
        mcp_server._tool_handlers = {}

        server = MultiTransportServer(mcp_server)

        # Test with no transports registered
        validation = server.validate_configuration()
        assert validation["valid"] is True  # No tools, so no routing needed

        # Add a tool but no transports
        mcp_server._tool_handlers = {"test_tool": lambda: None}
        server._discover_tools()

        validation = server.validate_configuration()
        assert validation["valid"] is False
        assert len(validation["errors"]) == 1

        # Add transport but make it inactive
        mock_transport = MockTransportManager("http")
        server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Transport is registered but not active
        validation = server.validate_configuration()
        assert validation["valid"] is False

    def test_metadata_and_configuration_consistency(self):
        """Test consistency between metadata and configuration."""
        mcp_server = MagicMock(spec=Server)

        @transport_info(TransportType.WEBHOOK, timeout=60, priority="high")
        def complex_tool():
            pass

        mcp_server._tool_handlers = {"complex_tool": complex_tool}

        server = MultiTransportServer(mcp_server)
        server._discover_tools()

        # Check that all metadata was preserved
        metadata = server.registry.get_tool_metadata("complex_tool")
        assert metadata is not None
        assert metadata.transport_type == TransportType.WEBHOOK
        assert metadata.metadata["timeout"] == 60
        assert metadata.metadata["priority"] == "high"

        # Check routing info includes metadata
        routing_info = server.get_tool_routing_info()
        complex_info = routing_info["complex_tool"]

        # The metadata should be in the ToolTransportInfo object
        tool_metadata = server.registry.get_tool_metadata("complex_tool")
        assert tool_metadata.metadata["timeout"] == 60
        assert tool_metadata.metadata["priority"] == "high"
