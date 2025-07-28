"""
Tests for MultiTransportServer integration and functionality.
"""

from unittest.mock import MagicMock, patch

import pytest
from mcp.server.lowlevel import Server
from mcp.types import Tool

from asyncmcp.multi_transport.registry import TransportType
from asyncmcp.multi_transport.routing import transport_info
from asyncmcp.multi_transport.server import MultiTransportServer


class MockTransportManager:
    """Mock transport manager for testing."""

    def __init__(self, is_running: bool = False):
        self._is_running = is_running
        self.start_called = False
        self.stop_called = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        self.start_called = True
        self._is_running = True

    async def stop(self) -> None:
        self.stop_called = True
        self._is_running = False


@pytest.fixture
def mcp_server():
    """Create a mock MCP server."""
    server = MagicMock(spec=Server)
    server._tool_handlers = {}
    return server


@pytest.fixture
def multi_server(mcp_server):
    """Create a MultiTransportServer instance."""
    return MultiTransportServer(mcp_server)


class TestMultiTransportServerInitialization:
    """Test MultiTransportServer initialization and basic properties."""

    def test_initialization(self, mcp_server):
        """Test server initialization with default parameters."""
        server = MultiTransportServer(mcp_server)

        assert server.mcp_server is mcp_server
        assert server.max_concurrent_sessions == 100
        assert server.enable_routing_validation is True
        assert not server.is_running
        assert server.registry is not None
        assert server.registry.mcp_server is mcp_server

    def test_initialization_with_custom_params(self, mcp_server):
        """Test server initialization with custom parameters."""
        server = MultiTransportServer(
            mcp_server=mcp_server, max_concurrent_sessions=50, enable_routing_validation=False
        )

        assert server.max_concurrent_sessions == 50
        assert server.enable_routing_validation is False

    def test_is_running_property(self, multi_server):
        """Test is_running property."""
        assert not multi_server.is_running


class TestTransportManagement:
    """Test transport registration and management."""

    def test_add_http_transport(self, multi_server):
        """Test adding HTTP transport."""
        multi_server.add_http_transport(
            host="localhost",
            port=8080,
            path="/api",
            stateless=True,
            json_response=True,
            tools=["http_tool"],
            is_default=True,
        )

        # Check transport was registered
        transport_info = multi_server.registry.get_transport_info(TransportType.HTTP)
        assert transport_info is not None
        assert transport_info.transport_type == TransportType.HTTP
        assert transport_info.tools == {"http_tool"}
        assert not transport_info.is_active

        # Check config
        expected_config = {"host": "localhost", "port": 8080, "path": "/api", "stateless": True, "json_response": True}
        assert transport_info.config == expected_config

        # Check default transport
        assert multi_server.registry.default_transport == TransportType.HTTP

    def test_add_webhook_transport(self, multi_server):
        """Test adding webhook transport."""
        mock_webhook_manager = MagicMock()

        multi_server.add_webhook_transport(
            webhook_manager=mock_webhook_manager, tools=["webhook_tool"], is_default=False, external_management=True
        )

        transport_info = multi_server.registry.get_transport_info(TransportType.WEBHOOK)
        assert transport_info is not None
        assert transport_info.tools == {"webhook_tool"}
        assert transport_info.config["manager"] is mock_webhook_manager

    def test_add_sqs_transport(self, multi_server):
        """Test adding SQS transport."""
        mock_sqs_manager = MagicMock()

        multi_server.add_sqs_transport(sqs_manager=mock_sqs_manager, tools=["sqs_tool"], is_default=True)

        transport_info = multi_server.registry.get_transport_info(TransportType.SQS)
        assert transport_info is not None
        assert transport_info.tools == {"sqs_tool"}
        assert multi_server.registry.default_transport == TransportType.SQS

    def test_add_sns_sqs_transport(self, multi_server):
        """Test adding SNS+SQS transport."""
        mock_sns_sqs_manager = MagicMock()

        multi_server.add_sns_sqs_transport(sns_sqs_manager=mock_sns_sqs_manager, tools=["sns_sqs_tool"])

        transport_info = multi_server.registry.get_transport_info(TransportType.SNS_SQS)
        assert transport_info is not None
        assert transport_info.tools == {"sns_sqs_tool"}


class TestToolDiscovery:
    """Test tool discovery and metadata registration."""

    def test_discover_tools_basic(self, multi_server):
        """Test basic tool discovery."""

        # Set up mock tool handlers
        def tool1_handler():
            pass

        def tool2_handler():
            pass

        multi_server.mcp_server._tool_handlers = {"tool1": tool1_handler, "tool2": tool2_handler}

        multi_server._discover_tools()

        # Check tools were discovered
        assert len(multi_server._registered_tools) == 2
        assert "tool1" in multi_server._registered_tools
        assert "tool2" in multi_server._registered_tools

        # Check Tool objects were created
        tool1_obj = multi_server._registered_tools["tool1"]
        assert isinstance(tool1_obj, Tool)
        assert tool1_obj.name == "tool1"

    def test_discover_tools_with_transport_metadata(self, multi_server):
        """Test tool discovery with transport metadata from decorators."""

        # Create decorated tool handlers
        @transport_info(TransportType.WEBHOOK, description="Webhook tool")
        def webhook_tool():
            pass

        @transport_info(TransportType.HTTP, description="HTTP tool")
        def http_tool():
            pass

        multi_server.mcp_server._tool_handlers = {"webhook_tool": webhook_tool, "http_tool": http_tool}

        multi_server._discover_tools()

        # Check metadata was registered
        webhook_metadata = multi_server.registry.get_tool_metadata("webhook_tool")
        assert webhook_metadata is not None
        assert webhook_metadata.transport_type == TransportType.WEBHOOK
        assert webhook_metadata.description == "Webhook tool"

        http_metadata = multi_server.registry.get_tool_metadata("http_tool")
        assert http_metadata is not None
        assert http_metadata.transport_type == TransportType.HTTP
        assert http_metadata.description == "HTTP tool"

    def test_discover_tools_no_handlers(self, multi_server):
        """Test tool discovery when server has no tool handlers."""
        # Server without _tool_handlers attribute
        delattr(multi_server.mcp_server, "_tool_handlers")

        multi_server._discover_tools()

        assert len(multi_server._registered_tools) == 0

    def test_discover_tools_empty_handlers(self, multi_server):
        """Test tool discovery with empty handlers."""
        multi_server.mcp_server._tool_handlers = {}

        multi_server._discover_tools()

        assert len(multi_server._registered_tools) == 0


class TestStatusAndValidation:
    """Test status reporting and configuration validation."""

    def test_get_transport_status(self, multi_server):
        """Test getting transport status."""
        # Add a transport
        mock_transport = MockTransportManager(is_running=True)
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP,
            transport_instance=mock_transport,
            config={"host": "localhost", "port": 8000},
            tools=["tool1"],
            is_default=True,
        )

        status = multi_server.get_transport_status()

        assert status["server_running"] is False
        assert len(status["transports"]) == 1

        http_status = status["transports"]["http"]
        assert http_status["type"] == "http"
        assert http_status["active"] is False
        assert http_status["tools"] == ["tool1"]
        assert http_status["running"] is True  # From mock transport

    def test_validate_configuration_success(self, multi_server):
        """Test successful configuration validation."""
        # Set up tools and transports
        multi_server._registered_tools = {
            "tool1": Tool(name="tool1", description="Test tool", inputSchema={"type": "object"})
        }

        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Make transport active for validation
        transport_info = multi_server.registry.get_transport_info(TransportType.HTTP)
        transport_info.is_active = True

        validation = multi_server.validate_configuration()

        assert validation["valid"] is True
        assert len(validation["errors"]) == 0
        assert validation["total_tools"] == 1
        assert validation["total_transports"] == 1
        assert validation["default_transport"] == "http"

    def test_validate_configuration_with_warnings(self, multi_server):
        """Test configuration validation with warnings."""
        # Tool prefers webhook but only HTTP available
        multi_server.registry.register_tool_metadata("tool1", TransportType.WEBHOOK)
        multi_server._registered_tools = {
            "tool1": Tool(name="tool1", description="Test tool", inputSchema={"type": "object"})
        }

        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Make transport active
        transport_info = multi_server.registry.get_transport_info(TransportType.HTTP)
        transport_info.is_active = True

        validation = multi_server.validate_configuration()

        assert validation["valid"] is True
        assert len(validation["warnings"]) == 1
        assert "prefers" in validation["warnings"][0]

    def test_validate_configuration_failure(self, multi_server):
        """Test configuration validation failure."""
        # Tool but no transports
        multi_server._registered_tools = {
            "tool1": Tool(name="tool1", description="Test tool", inputSchema={"type": "object"})
        }

        validation = multi_server.validate_configuration()

        assert validation["valid"] is False
        assert len(validation["errors"]) == 1


class TestRoutingAndToolManagement:
    """Test routing decisions and tool management."""

    @pytest.mark.anyio
    async def test_route_tool_call(self, multi_server):
        """Test tool call routing."""
        # Set up transport and tool
        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Make transport active
        transport_info = multi_server.registry.get_transport_info(TransportType.HTTP)
        transport_info.is_active = True

        # Route tool call
        result = await multi_server.route_tool_call("test_tool", {"arg": "value"})

        assert result == TransportType.HTTP

    @pytest.mark.anyio
    async def test_route_tool_call_with_explicit_transport(self, multi_server):
        """Test routing with explicit transport preference."""
        # Register tool with specific transport
        multi_server.registry.register_tool_metadata("webhook_tool", TransportType.WEBHOOK)

        # Register both transports
        mock_http = MockTransportManager()
        mock_webhook = MockTransportManager()

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_http, config={}, is_default=True
        )
        multi_server.registry.register_transport(
            transport_type=TransportType.WEBHOOK, transport_instance=mock_webhook, config={}
        )

        # Make both active
        multi_server.registry.get_transport_info(TransportType.HTTP).is_active = True
        multi_server.registry.get_transport_info(TransportType.WEBHOOK).is_active = True

        # Route tool call
        result = await multi_server.route_tool_call("webhook_tool", {})

        assert result == TransportType.WEBHOOK

    def test_get_tool_routing_info(self, multi_server):
        """Test getting tool routing information."""
        # Set up tools with metadata
        multi_server.registry.register_tool_metadata(
            "explicit_tool", TransportType.WEBHOOK, description="Explicit webhook tool"
        )

        multi_server._registered_tools = {
            "explicit_tool": Tool(name="explicit_tool", description="Test", inputSchema={}),
            "default_tool": Tool(name="default_tool", description="Test", inputSchema={}),
        }

        # Set up transport
        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        routing_info = multi_server.get_tool_routing_info()

        assert len(routing_info) == 2

        explicit_info = routing_info["explicit_tool"]
        assert explicit_info["assigned_transport"] == "http"  # Only HTTP available
        assert explicit_info["has_explicit_transport"] is True
        assert explicit_info["explicit_transport"] == "webhook"

        default_info = routing_info["default_tool"]
        assert default_info["assigned_transport"] == "http"
        assert default_info["has_explicit_transport"] is False
        assert default_info["explicit_transport"] is None

    def test_get_tool_routing_map(self, multi_server):
        """Test getting tool routing map."""
        multi_server._registered_tools = {
            "tool1": Tool(name="tool1", description="Test", inputSchema={}),
            "tool2": Tool(name="tool2", description="Test", inputSchema={}),
        }

        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        routing_map = multi_server.get_tool_routing_map()

        assert routing_map == {"tool1": "http", "tool2": "http"}


class TestServerLifecycle:
    """Test server lifecycle management."""

    @pytest.mark.anyio
    async def test_run_context_manager_basic(self, multi_server):
        """Test basic server run context manager."""
        # Disable validation for this test
        multi_server.enable_routing_validation = False

        async with multi_server.run() as server:
            assert server is multi_server
            assert multi_server.is_running

        # After context exit, should be stopped
        assert not multi_server.is_running

    @pytest.mark.anyio
    async def test_run_with_validation_enabled(self, multi_server):
        """Test server run with validation enabled."""
        # Set up valid configuration
        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        async with multi_server.run():
            assert multi_server.is_running
            # Transport should be started
            assert mock_transport.start_called

    @pytest.mark.anyio
    async def test_run_validation_enabled_success(self, multi_server):
        """Test server run with validation enabled passes when valid."""
        # Add a tool and a transport
        multi_server._registered_tools = {"tool1": Tool(name="tool1", description="Test", inputSchema={})}

        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Should succeed with validation enabled
        multi_server.enable_routing_validation = True

        async with multi_server.run():
            assert multi_server.is_running

    @pytest.mark.anyio
    async def test_run_already_running_fails(self, multi_server):
        """Test that running already running server fails."""
        multi_server.enable_routing_validation = False

        async with multi_server.run():
            with pytest.raises(RuntimeError, match="already running"):
                async with multi_server.run():
                    pass

    @pytest.mark.anyio
    async def test_cleanup_on_exception(self, multi_server):
        """Test cleanup when exception occurs during startup."""
        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}
        )

        # Mock start_all_transports to raise exception
        with patch.object(multi_server.registry, "start_all_transports", side_effect=Exception("Start failed")):
            with pytest.raises(Exception):  # Don't match specific exception due to anyio wrapping
                async with multi_server.run():
                    pass

        # Should be properly cleaned up
        assert not multi_server.is_running

    @pytest.mark.anyio
    async def test_transport_lifecycle_management(self, multi_server):
        """Test that transports are properly started and stopped."""
        mock_transport1 = MockTransportManager()
        mock_transport2 = MockTransportManager()

        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport1, config={}
        )
        multi_server.registry.register_transport(
            transport_type=TransportType.WEBHOOK, transport_instance=mock_transport2, config={}
        )

        multi_server.enable_routing_validation = False

        async with multi_server.run():
            # Both transports should be started
            assert mock_transport1.start_called
            assert mock_transport2.start_called
            assert mock_transport1.is_running
            assert mock_transport2.is_running

        # After context exit, both should be stopped
        assert mock_transport1.stop_called
        assert mock_transport2.stop_called
        assert not mock_transport1.is_running
        assert not mock_transport2.is_running


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_get_tool_routing_info_with_errors(self, multi_server):
        """Test tool routing info when routing fails."""
        # Tool with no available transports
        multi_server._registered_tools = {"problem_tool": Tool(name="problem_tool", description="Test", inputSchema={})}

        routing_info = multi_server.get_tool_routing_info()

        problem_info = routing_info["problem_tool"]
        assert "error" in problem_info
        assert problem_info["assigned_transport"] is None

    def test_get_tool_routing_map_with_fallback(self, multi_server):
        """Test tool routing map with fallback to default."""
        multi_server._registered_tools = {"tool1": Tool(name="tool1", description="Test", inputSchema={})}

        # Set up default transport
        mock_transport = MockTransportManager()
        multi_server.registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Mock make_routing_decision to raise exception
        with patch.object(multi_server.registry, "make_routing_decision", side_effect=Exception("Routing failed")):
            routing_map = multi_server.get_tool_routing_map()

            # Should fall back to default transport
            assert routing_map["tool1"] == "http"
