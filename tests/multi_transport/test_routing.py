"""
Tests for the new routing system without global state.
"""

from unittest.mock import MagicMock

import pytest
from mcp.server.lowlevel import Server

from asyncmcp.multi_transport.registry import TransportRegistry, TransportType
from asyncmcp.multi_transport.routing import register_tool_with_server, transport_info


@pytest.fixture
def mock_server():
    """Create a mock MultiTransportServer."""
    server = MagicMock()
    server.registry = MagicMock(spec=TransportRegistry)
    return server


class TestTransportInfoDecorator:
    """Test the @transport_info decorator functionality."""

    def test_decorator_basic_usage(self):
        """Test basic decorator usage."""

        @transport_info(TransportType.WEBHOOK, description="Test webhook tool")
        def test_tool():
            return "test result"

        # Check decorator added attributes
        assert hasattr(test_tool, "_transport_type")
        assert hasattr(test_tool, "_transport_description")
        assert hasattr(test_tool, "_transport_metadata")

        assert test_tool._transport_type == TransportType.WEBHOOK
        assert test_tool._transport_description == "Test webhook tool"
        assert test_tool._transport_metadata == {}

        # Check function still works
        assert test_tool() == "test result"

    def test_decorator_with_metadata(self):
        """Test decorator with additional metadata."""

        @transport_info(TransportType.HTTP, description="Fast HTTP tool", timeout=30, priority="high")
        def fast_tool():
            pass

        assert fast_tool._transport_type == TransportType.HTTP
        assert fast_tool._transport_description == "Fast HTTP tool"
        assert fast_tool._transport_metadata == {"timeout": 30, "priority": "high"}

    def test_decorator_minimal_usage(self):
        """Test decorator with minimal parameters."""

        @transport_info(TransportType.SQS)
        def sqs_tool():
            pass

        assert sqs_tool._transport_type == TransportType.SQS
        assert sqs_tool._transport_description is None
        assert sqs_tool._transport_metadata == {}

    def test_decorator_preserves_function_metadata(self):
        """Test that decorator preserves original function metadata."""

        @transport_info(TransportType.WEBHOOK)
        def documented_tool():
            """This is a documented tool."""
            return 42

        assert documented_tool.__name__ == "documented_tool"
        assert documented_tool.__doc__ == "This is a documented tool."
        assert documented_tool() == 42

    def test_decorator_on_async_function(self):
        """Test decorator works with async functions."""

        @transport_info(TransportType.WEBHOOK, description="Async tool")
        async def async_tool():
            return "async result"

        assert async_tool._transport_type == TransportType.WEBHOOK
        assert async_tool._transport_description == "Async tool"

    def test_decorator_on_method(self):
        """Test decorator works with class methods."""

        class ToolClass:
            @transport_info(TransportType.HTTP, description="Method tool")
            def tool_method(self):
                return "method result"

        tool_instance = ToolClass()
        assert tool_instance.tool_method._transport_type == TransportType.HTTP
        assert tool_instance.tool_method._transport_description == "Method tool"


class TestRegisterToolWithServer:
    """Test the register_tool_with_server function."""

    def test_register_decorated_function(self, mock_server):
        """Test registering a decorated function with server."""

        @transport_info(TransportType.WEBHOOK, description="Test tool", custom="value")
        def test_tool():
            pass

        register_tool_with_server(mock_server, test_tool)

        # Check that registry.register_tool_metadata was called
        mock_server.registry.register_tool_metadata.assert_called_once_with(
            tool_name="test_tool", transport_type=TransportType.WEBHOOK, description="Test tool", custom="value"
        )

    def test_register_undecorated_function(self, mock_server):
        """Test registering an undecorated function (should be no-op)."""

        def regular_function():
            pass

        register_tool_with_server(mock_server, regular_function)

        # Should not call registry method
        mock_server.registry.register_tool_metadata.assert_not_called()

    def test_register_function_without_name(self, mock_server):
        """Test registering function without proper __name__ attribute."""

        # Create a function-like object without proper __name__
        class FakeFunction:
            def __call__(self):
                return None

        func_obj = FakeFunction()

        # Add transport attributes
        func_obj._transport_type = TransportType.HTTP
        func_obj._transport_description = "Lambda tool"
        func_obj._transport_metadata = {}

        register_tool_with_server(mock_server, func_obj)

        # Should use "unknown" as tool name
        mock_server.registry.register_tool_metadata.assert_called_once_with(
            tool_name="unknown", transport_type=TransportType.HTTP, description="Lambda tool"
        )

    def test_register_function_minimal_attributes(self, mock_server):
        """Test registering function with minimal transport attributes."""

        def minimal_tool():
            pass

        # Set only required attribute
        minimal_tool._transport_type = TransportType.SQS

        register_tool_with_server(mock_server, minimal_tool)

        mock_server.registry.register_tool_metadata.assert_called_once_with(
            tool_name="minimal_tool", transport_type=TransportType.SQS, description=None
        )

    def test_register_function_with_all_attributes(self, mock_server):
        """Test registering function with all transport attributes."""

        def full_tool():
            pass

        full_tool._transport_type = TransportType.SNS_SQS
        full_tool._transport_description = "Full featured tool"
        full_tool._transport_metadata = {"timeout": 60, "retries": 3, "priority": "low"}

        register_tool_with_server(mock_server, full_tool)

        mock_server.registry.register_tool_metadata.assert_called_once_with(
            tool_name="full_tool",
            transport_type=TransportType.SNS_SQS,
            description="Full featured tool",
            timeout=60,
            retries=3,
            priority="low",
        )


class TestRoutingSystemIntegration:
    """Test integration between decorator and registry system."""

    def test_full_routing_workflow(self):
        """Test the complete workflow from decoration to routing."""
        # Create actual registry (not mock)
        mcp_server = MagicMock(spec=Server)
        registry = TransportRegistry(mcp_server)

        # Create mock server with real registry
        mock_server = MagicMock()
        mock_server.registry = registry

        # Decorate functions
        @transport_info(TransportType.WEBHOOK, description="Async processor")
        def process_async():
            pass

        @transport_info(TransportType.HTTP, description="Fast fetcher")
        def fetch_sync():
            pass

        def default_tool():  # No decorator
            pass

        # Register with server
        register_tool_with_server(mock_server, process_async)
        register_tool_with_server(mock_server, fetch_sync)
        register_tool_with_server(mock_server, default_tool)

        # Check metadata was registered
        async_metadata = registry.get_tool_metadata("process_async")
        assert async_metadata is not None
        assert async_metadata.transport_type == TransportType.WEBHOOK
        assert async_metadata.description == "Async processor"

        sync_metadata = registry.get_tool_metadata("fetch_sync")
        assert sync_metadata is not None
        assert sync_metadata.transport_type == TransportType.HTTP
        assert sync_metadata.description == "Fast fetcher"

        default_metadata = registry.get_tool_metadata("default_tool")
        assert default_metadata is None  # No decorator, no metadata

        # Test routing decisions
        routing_decision = registry.make_routing_decision("process_async", [TransportType.HTTP, TransportType.WEBHOOK])
        assert routing_decision == TransportType.WEBHOOK

        routing_decision = registry.make_routing_decision("fetch_sync", [TransportType.HTTP, TransportType.WEBHOOK])
        assert routing_decision == TransportType.HTTP

    def test_decorator_with_server_scoped_registration(self):
        """Test that decorators work correctly with server-scoped registration."""
        # Create two separate servers
        mcp_server1 = MagicMock(spec=Server)
        mcp_server2 = MagicMock(spec=Server)

        registry1 = TransportRegistry(mcp_server1)
        registry2 = TransportRegistry(mcp_server2)

        mock_server1 = MagicMock()
        mock_server1.registry = registry1

        mock_server2 = MagicMock()
        mock_server2.registry = registry2

        # Same decorated function
        @transport_info(TransportType.WEBHOOK, description="Shared tool")
        def shared_tool():
            pass

        # Register with both servers
        register_tool_with_server(mock_server1, shared_tool)
        register_tool_with_server(mock_server2, shared_tool)

        # Each server should have its own metadata
        metadata1 = registry1.get_tool_metadata("shared_tool")
        metadata2 = registry2.get_tool_metadata("shared_tool")

        assert metadata1 is not None
        assert metadata2 is not None
        assert metadata1 is not metadata2  # Different instances
        assert metadata1.transport_type == metadata2.transport_type


class TestDecoratorErrorHandling:
    """Test error handling in the routing system."""

    def test_decorator_with_invalid_transport_type(self):
        """Test decorator behavior with invalid transport type."""

        # This should work at decoration time, validation happens at routing time
        @transport_info("invalid_transport")  # type: ignore
        def bad_tool():
            pass

        assert bad_tool._transport_type == "invalid_transport"

    def test_register_with_broken_function_object(self, mock_server):
        """Test registering with malformed function object."""

        class BrokenFunction:
            def __init__(self):
                self._transport_type = TransportType.HTTP
                # Missing other attributes

        broken_func = BrokenFunction()

        # Should handle missing attributes gracefully
        register_tool_with_server(mock_server, broken_func)

        mock_server.registry.register_tool_metadata.assert_called_once_with(
            tool_name="unknown", transport_type=TransportType.HTTP, description=None
        )


class TestBackwardCompatibilityAndMigration:
    """Test that the new system maintains expected behavior."""

    def test_no_global_state_pollution(self):
        """Test that decorating functions doesn't create global state."""

        @transport_info(TransportType.WEBHOOK)
        def test_tool1():
            pass

        @transport_info(TransportType.HTTP)
        def test_tool2():
            pass

        # Functions should only have local attributes, no global registry
        assert hasattr(test_tool1, "_transport_type")
        assert hasattr(test_tool2, "_transport_type")

        # No global state should exist - specifically check for tool metadata dictionaries
        import asyncmcp.multi_transport.routing as routing_module

        # Check that there are no global tool metadata dictionaries
        module_vars = vars(routing_module)

        # Look for any variable that looks like a tool metadata registry
        tool_metadata_vars = [
            var
            for name, var in module_vars.items()
            if isinstance(var, dict)
            and ("tool" in name.lower() or "metadata" in name.lower() or "transport" in name.lower())
            and len(var) > 0
        ]

        # Should not find any tool metadata dictionaries
        assert len(tool_metadata_vars) == 0

    def test_multiple_decorations_independent(self):
        """Test that multiple decorations are independent."""

        @transport_info(TransportType.WEBHOOK, timeout=30)
        def tool1():
            pass

        @transport_info(TransportType.HTTP, timeout=10)
        def tool2():
            pass

        # Each function should have its own independent metadata
        assert tool1._transport_type == TransportType.WEBHOOK
        assert tool1._transport_metadata["timeout"] == 30

        assert tool2._transport_type == TransportType.HTTP
        assert tool2._transport_metadata["timeout"] == 10

        # Modifying one shouldn't affect the other
        tool1._transport_metadata["new_field"] = "value1"
        assert "new_field" not in tool2._transport_metadata
