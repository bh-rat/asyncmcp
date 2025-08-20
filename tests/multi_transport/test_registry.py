"""
Comprehensive tests for TransportRegistry functionality.
"""

from unittest.mock import MagicMock

import pytest
from mcp.server.lowlevel import Server

from asyncmcp.multi_transport.registry import (
    ToolTransportInfo,
    TransportInfo,
    TransportRegistry,
    TransportType,
)


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
    return MagicMock(spec=Server)


@pytest.fixture
def registry(mcp_server):
    """Create a TransportRegistry instance."""
    return TransportRegistry(mcp_server)


@pytest.fixture
def mock_transport():
    """Create a mock transport manager."""
    return MockTransportManager()


class TestTransportRegistry:
    """Test TransportRegistry core functionality."""

    def test_registry_initialization(self, mcp_server):
        """Test registry initialization."""
        registry = TransportRegistry(mcp_server)

        assert registry.mcp_server is mcp_server
        assert len(registry.get_all_transports()) == 0
        assert registry.default_transport is None
        assert len(registry.get_all_tool_metadata()) == 0

    def test_register_transport_basic(self, registry, mock_transport):
        """Test basic transport registration."""
        config = {"host": "localhost", "port": 8000}
        tools = ["tool1", "tool2"]

        registry.register_transport(
            transport_type=TransportType.HTTP,
            transport_instance=mock_transport,
            config=config,
            tools=tools,
            is_default=True,
        )

        # Check transport was registered
        all_transports = registry.get_all_transports()
        assert len(all_transports) == 1
        assert TransportType.HTTP in all_transports

        transport_info = all_transports[TransportType.HTTP]
        assert transport_info.transport_type == TransportType.HTTP
        assert transport_info.transport_instance is mock_transport
        assert transport_info.config == config
        assert transport_info.tools == {"tool1", "tool2"}
        assert transport_info.is_active is False

        # Check default transport
        assert registry.default_transport == TransportType.HTTP

        # Check tool mapping
        tool_mapping = registry.get_tool_transport_mapping()
        assert tool_mapping["tool1"] == TransportType.HTTP
        assert tool_mapping["tool2"] == TransportType.HTTP

    def test_register_duplicate_transport_fails(self, registry, mock_transport):
        """Test that registering duplicate transport type fails."""
        registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, tools=[]
        )

        with pytest.raises(ValueError, match="already registered"):
            registry.register_transport(
                transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, tools=[]
            )

    def test_unregister_transport(self, registry, mock_transport):
        """Test transport unregistration."""
        # Register first
        registry.register_transport(
            transport_type=TransportType.HTTP,
            transport_instance=mock_transport,
            config={},
            tools=["tool1"],
            is_default=True,
        )

        # Unregister
        registry.unregister_transport(TransportType.HTTP)

        # Check it's gone
        assert len(registry.get_all_transports()) == 0
        assert registry.default_transport is None
        assert len(registry.get_tool_transport_mapping()) == 0

    def test_unregister_nonexistent_transport(self, registry):
        """Test unregistering non-existent transport."""
        # Should not raise, just log warning
        registry.unregister_transport(TransportType.HTTP)

    def test_set_transport_tools(self, registry, mock_transport):
        """Test updating tools for a transport."""
        # Register transport
        registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, tools=["tool1", "tool2"]
        )

        # Update tools
        registry.set_transport_tools(TransportType.HTTP, ["tool3", "tool4"])

        # Check tools were updated
        transport_info = registry.get_transport_info(TransportType.HTTP)
        assert transport_info.tools == {"tool3", "tool4"}

        tool_mapping = registry.get_tool_transport_mapping()
        assert "tool1" not in tool_mapping
        assert "tool2" not in tool_mapping
        assert tool_mapping["tool3"] == TransportType.HTTP
        assert tool_mapping["tool4"] == TransportType.HTTP

    def test_set_tools_for_nonexistent_transport_fails(self, registry):
        """Test setting tools for non-existent transport fails."""
        with pytest.raises(ValueError, match="not registered"):
            registry.set_transport_tools(TransportType.HTTP, ["tool1"])

    def test_default_transport_management(self, registry, mock_transport):
        """Test default transport management."""
        # No default initially
        assert registry.default_transport is None

        # Register first transport (should become default)
        registry.register_transport(transport_type=TransportType.HTTP, transport_instance=mock_transport, config={})
        assert registry.default_transport == TransportType.HTTP

        # Register second transport (should not change default)
        mock_transport2 = MockTransportManager()
        registry.register_transport(transport_type=TransportType.WEBHOOK, transport_instance=mock_transport2, config={})
        assert registry.default_transport == TransportType.HTTP

        # Explicitly set default
        registry.default_transport = TransportType.WEBHOOK
        assert registry.default_transport == TransportType.WEBHOOK

    def test_set_invalid_default_transport_fails(self, registry):
        """Test setting invalid default transport fails."""
        with pytest.raises(ValueError, match="not registered"):
            registry.default_transport = TransportType.HTTP

    @pytest.mark.anyio
    async def test_start_transport(self, registry, mock_transport):
        """Test starting a transport."""
        registry.register_transport(transport_type=TransportType.HTTP, transport_instance=mock_transport, config={})

        await registry.start_transport(TransportType.HTTP)

        assert mock_transport.start_called
        assert mock_transport.is_running

        transport_info = registry.get_transport_info(TransportType.HTTP)
        assert transport_info.is_active

    @pytest.mark.anyio
    async def test_start_nonexistent_transport_fails(self, registry):
        """Test starting non-existent transport fails."""
        with pytest.raises(ValueError, match="not registered"):
            await registry.start_transport(TransportType.HTTP)

    @pytest.mark.anyio
    async def test_start_already_active_transport(self, registry, mock_transport):
        """Test starting already active transport."""
        registry.register_transport(transport_type=TransportType.HTTP, transport_instance=mock_transport, config={})

        # Start first time
        await registry.start_transport(TransportType.HTTP)
        mock_transport.start_called = False  # Reset flag

        # Start second time (should be no-op)
        await registry.start_transport(TransportType.HTTP)
        assert not mock_transport.start_called

    @pytest.mark.anyio
    async def test_stop_transport(self, registry, mock_transport):
        """Test stopping a transport."""
        registry.register_transport(transport_type=TransportType.HTTP, transport_instance=mock_transport, config={})

        # Start then stop
        await registry.start_transport(TransportType.HTTP)
        await registry.stop_transport(TransportType.HTTP)

        assert mock_transport.stop_called
        assert not mock_transport.is_running

        transport_info = registry.get_transport_info(TransportType.HTTP)
        assert not transport_info.is_active

    @pytest.mark.anyio
    async def test_start_stop_all_transports(self, registry):
        """Test starting and stopping all transports."""
        # Register multiple transports
        mock_transport1 = MockTransportManager()
        mock_transport2 = MockTransportManager()

        registry.register_transport(transport_type=TransportType.HTTP, transport_instance=mock_transport1, config={})
        registry.register_transport(transport_type=TransportType.WEBHOOK, transport_instance=mock_transport2, config={})

        # Start all
        await registry.start_all_transports()

        assert mock_transport1.start_called
        assert mock_transport2.start_called
        assert len(registry.get_active_transports()) == 2

        # Stop all
        await registry.stop_all_transports()

        assert mock_transport1.stop_called
        assert mock_transport2.stop_called
        assert len(registry.get_active_transports()) == 0


class TestToolMetadataManagement:
    """Test tool metadata management functionality."""

    def test_register_tool_metadata(self, registry):
        """Test registering tool metadata."""
        registry.register_tool_metadata(
            tool_name="test_tool",
            transport_type=TransportType.WEBHOOK,
            description="Test webhook tool",
            custom_field="custom_value",
        )

        metadata = registry.get_tool_metadata("test_tool")
        assert metadata is not None
        assert metadata.tool_name == "test_tool"
        assert metadata.transport_type == TransportType.WEBHOOK
        assert metadata.description == "Test webhook tool"
        assert metadata.metadata["custom_field"] == "custom_value"

        # Check it was added to tool-transport mapping
        tool_mapping = registry.get_tool_transport_mapping()
        assert tool_mapping["test_tool"] == TransportType.WEBHOOK

    def test_get_nonexistent_tool_metadata(self, registry):
        """Test getting metadata for non-existent tool."""
        metadata = registry.get_tool_metadata("nonexistent")
        assert metadata is None

    def test_get_all_tool_metadata(self, registry):
        """Test getting all tool metadata."""
        registry.register_tool_metadata("tool1", TransportType.HTTP)
        registry.register_tool_metadata("tool2", TransportType.WEBHOOK)

        all_metadata = registry.get_all_tool_metadata()
        assert len(all_metadata) == 2
        assert "tool1" in all_metadata
        assert "tool2" in all_metadata

    def test_get_tools_for_transport(self, registry):
        """Test getting tools for specific transport."""
        registry.register_tool_metadata("tool1", TransportType.HTTP)
        registry.register_tool_metadata("tool2", TransportType.HTTP)
        registry.register_tool_metadata("tool3", TransportType.WEBHOOK)

        http_tools = registry.get_tools_for_transport(TransportType.HTTP)
        webhook_tools = registry.get_tools_for_transport(TransportType.WEBHOOK)

        assert set(http_tools) == {"tool1", "tool2"}
        assert set(webhook_tools) == {"tool3"}

    def test_unregister_tool_metadata(self, registry):
        """Test unregistering tool metadata."""
        registry.register_tool_metadata("test_tool", TransportType.HTTP)

        # Unregister
        result = registry.unregister_tool_metadata("test_tool")
        assert result is True

        # Check it's gone
        metadata = registry.get_tool_metadata("test_tool")
        assert metadata is None

        tool_mapping = registry.get_tool_transport_mapping()
        assert "test_tool" not in tool_mapping

    def test_unregister_nonexistent_tool_metadata(self, registry):
        """Test unregistering non-existent tool metadata."""
        result = registry.unregister_tool_metadata("nonexistent")
        assert result is False


class TestRoutingDecisions:
    """Test routing decision functionality."""

    def test_make_routing_decision_explicit_transport(self, registry):
        """Test routing decision with explicit transport."""
        # Register tool metadata
        registry.register_tool_metadata("tool1", TransportType.WEBHOOK)

        # Make routing decision
        decision = registry.make_routing_decision("tool1", [TransportType.HTTP, TransportType.WEBHOOK])
        assert decision == TransportType.WEBHOOK

    def test_make_routing_decision_default_transport(self, registry, mock_transport):
        """Test routing decision falling back to default transport."""
        # Register transport and set as default
        registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Make routing decision for tool without explicit transport
        decision = registry.make_routing_decision("unknown_tool", [TransportType.HTTP, TransportType.WEBHOOK])
        assert decision == TransportType.HTTP

    def test_make_routing_decision_first_available(self, registry):
        """Test routing decision using first available transport."""
        # No default set, should use first available
        decision = registry.make_routing_decision("unknown_tool", [TransportType.WEBHOOK, TransportType.HTTP])
        assert decision == TransportType.WEBHOOK

    def test_make_routing_decision_no_transports_fails(self, registry):
        """Test routing decision with no available transports fails."""
        with pytest.raises(ValueError, match="No suitable transport found"):
            registry.make_routing_decision("tool1", [])

    def test_make_routing_decision_preferred_not_available(self, registry):
        """Test routing decision when preferred transport not available."""
        registry.register_tool_metadata("tool1", TransportType.WEBHOOK)

        # Webhook not available, should use first available
        decision = registry.make_routing_decision("tool1", [TransportType.HTTP])
        assert decision == TransportType.HTTP

    def test_validate_tool_routing_success(self, registry, mock_transport):
        """Test successful tool routing validation."""
        # Set up transports
        registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Set up tool metadata
        registry.register_tool_metadata("tool1", TransportType.HTTP)

        # Make transport active
        transport_info = registry.get_transport_info(TransportType.HTTP)
        transport_info.is_active = True

        result = registry.validate_tool_routing(["tool1", "tool2"])

        assert result["valid"] is True
        assert len(result["errors"]) == 0
        assert result["tool_routing"]["tool1"] == TransportType.HTTP
        assert result["tool_routing"]["tool2"] == TransportType.HTTP  # Uses default

    def test_validate_tool_routing_with_warnings(self, registry, mock_transport):
        """Test tool routing validation with warnings."""
        # Set up transports
        registry.register_transport(
            transport_type=TransportType.HTTP, transport_instance=mock_transport, config={}, is_default=True
        )

        # Tool prefers webhook but only HTTP available
        registry.register_tool_metadata("tool1", TransportType.WEBHOOK)

        # Make transport active
        transport_info = registry.get_transport_info(TransportType.HTTP)
        transport_info.is_active = True

        result = registry.validate_tool_routing(["tool1"])

        assert result["valid"] is True
        assert len(result["warnings"]) == 1
        assert "prefers" in result["warnings"][0]
        assert result["tool_routing"]["tool1"] == TransportType.HTTP

    def test_validate_tool_routing_failure(self, registry):
        """Test tool routing validation failure."""
        # No transports registered
        result = registry.validate_tool_routing(["tool1"])

        assert result["valid"] is False
        assert len(result["errors"]) == 1
        assert "No suitable transport found" in result["errors"][0]


class TestTransportInfo:
    """Test TransportInfo dataclass."""

    def test_transport_info_creation(self):
        """Test TransportInfo creation."""
        mock_transport = MockTransportManager()
        config = {"host": "localhost"}
        tools = {"tool1", "tool2"}

        info = TransportInfo(
            transport_type=TransportType.HTTP,
            transport_instance=mock_transport,
            config=config,
            tools=tools,
            is_active=True,
        )

        assert info.transport_type == TransportType.HTTP
        assert info.transport_instance is mock_transport
        assert info.config == config
        assert info.tools == tools
        assert info.is_active is True


class TestToolTransportInfo:
    """Test ToolTransportInfo dataclass."""

    def test_tool_transport_info_creation(self):
        """Test ToolTransportInfo creation."""
        metadata = {"custom": "value"}

        info = ToolTransportInfo(
            tool_name="test_tool",
            transport_type=TransportType.WEBHOOK,
            description="Test description",
            metadata=metadata,
        )

        assert info.tool_name == "test_tool"
        assert info.transport_type == TransportType.WEBHOOK
        assert info.description == "Test description"
        assert info.metadata == metadata

    def test_tool_transport_info_defaults(self):
        """Test ToolTransportInfo with default values."""
        info = ToolTransportInfo(tool_name="test_tool", transport_type=TransportType.HTTP)

        assert info.description is None
        assert info.metadata == {}
