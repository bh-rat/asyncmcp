"""
Multi-transport server orchestrator for AsyncMCP.

This module provides the MultiTransportServer class that manages multiple
transport types simultaneously and routes requests based on tool associations.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, List, Optional

import anyio
from mcp.server.lowlevel import Server
from mcp.types import Tool

from .adapters import HttpTransportAdapter, WebhookTransportAdapter
from .registry import TransportRegistry, TransportType
from .routing import register_tool_with_server

logger = logging.getLogger(__name__)


class MultiTransportServer:
    """
    Multi-transport server that manages multiple transport types simultaneously.

    This class orchestrates multiple transport instances, handles dynamic routing
    based on tool associations, and provides a unified interface for MCP servers
    that need to support multiple transport types.
    """

    def __init__(self, mcp_server: Server, max_concurrent_sessions: int = 100, enable_routing_validation: bool = True):
        self.mcp_server = mcp_server
        self.max_concurrent_sessions = max_concurrent_sessions
        self.enable_routing_validation = enable_routing_validation

        # Core components
        self.registry = TransportRegistry(mcp_server)
        self._task_group: Optional[anyio.abc.TaskGroup] = None
        self._is_running = False

        # Tool introspection
        self._registered_tools: Dict[str, Tool] = {}
        self._tool_handlers: Dict[str, Callable] = {}

    @property
    def is_running(self) -> bool:
        """Check if the multi-transport server is running."""
        return self._is_running

    def add_http_transport(
        self,
        host: str = "localhost",
        port: int = 8000,
        path: str = "/mcp",
        stateless: bool = False,
        json_response: bool = False,
        tools: Optional[List[str]] = None,
        is_default: bool = False,
    ) -> None:
        """
        Add StreamableHTTP transport to the server.

        Args:
            host: Host to bind the HTTP server to
            port: Port to bind the HTTP server to
            path: HTTP path for MCP communication
            stateless: Whether to run in stateless mode
            json_response: Whether to return JSON responses instead of SSE
            tools: List of tool names that should use this transport
            is_default: Whether this should be the default transport
        """
        adapter = HttpTransportAdapter(
            mcp_server=self.mcp_server,
            host=host,
            port=port,
            path=path,
            stateless=stateless,
            json_response=json_response,
        )

        config = {"host": host, "port": port, "path": path, "stateless": stateless, "json_response": json_response}

        self.registry.register_transport(
            transport_type=TransportType.HTTP,
            transport_instance=adapter,
            config=config,
            tools=tools,
            is_default=is_default,
        )

    def add_webhook_transport(
        self,
        webhook_manager: Any,
        tools: Optional[List[str]] = None,
        is_default: bool = False,
        external_management: bool = False,
    ) -> None:
        """
        Add webhook transport to the server.

        Args:
            webhook_manager: WebhookSessionManager instance
            tools: List of tool names that should use this transport
            is_default: Whether this should be the default transport
            external_management: Whether the webhook manager is managed externally
        """
        adapter = WebhookTransportAdapter(webhook_manager, external_management=external_management)

        config = {"manager": webhook_manager}

        self.registry.register_transport(
            transport_type=TransportType.WEBHOOK,
            transport_instance=adapter,
            config=config,
            tools=tools,
            is_default=is_default,
        )

    def add_sqs_transport(self, sqs_manager: Any, tools: Optional[List[str]] = None, is_default: bool = False) -> None:
        """
        Add SQS transport to the server.

        Args:
            sqs_manager: SQS session manager instance
            tools: List of tool names that should use this transport
            is_default: Whether this should be the default transport
        """
        # Note: SQS manager should be wrapped with appropriate adapter
        config = {"manager": sqs_manager}

        self.registry.register_transport(
            transport_type=TransportType.SQS,
            transport_instance=sqs_manager,
            config=config,
            tools=tools,
            is_default=is_default,
        )

    def add_sns_sqs_transport(
        self, sns_sqs_manager: Any, tools: Optional[List[str]] = None, is_default: bool = False
    ) -> None:
        """
        Add SNS+SQS transport to the server.

        Args:
            sns_sqs_manager: SNS+SQS session manager instance
            tools: List of tool names that should use this transport
            is_default: Whether this should be the default transport
        """
        config = {"manager": sns_sqs_manager}

        self.registry.register_transport(
            transport_type=TransportType.SNS_SQS,
            transport_instance=sns_sqs_manager,
            config=config,
            tools=tools,
            is_default=is_default,
        )

    def get_transport_status(self) -> Dict[str, Any]:
        """
        Get status information for all transports.

        Returns:
            Dictionary with status information for each transport
        """
        status = {"server_running": self._is_running, "transports": {}}

        for transport_type, info in self.registry.get_all_transports().items():
            transport_status = {
                "type": transport_type.value,
                "active": info.is_active,
                "tools": list(info.tools),
                "config": info.config,
            }

            # Add transport-specific status if available
            if hasattr(info.transport_instance, "is_running"):
                transport_status["running"] = info.transport_instance.is_running

            status["transports"][transport_type.value] = transport_status

        return status

    def validate_configuration(self) -> Dict[str, Any]:
        """
        Validate the current multi-transport configuration.

        Returns:
            Dictionary with validation results
        """
        # Get all tools from MCP server
        tool_names = list(self._registered_tools.keys())

        validation_result = self.registry.validate_tool_routing(tool_names)

        # Add additional validation info
        validation_result.update(
            {
                "total_tools": len(tool_names),
                "total_transports": len(self.registry.get_all_transports()),
                "default_transport": self.registry.default_transport.value if self.registry.default_transport else None,
                "tool_transport_metadata": {
                    name: info.transport_type.value for name, info in self.registry.get_all_tool_metadata().items()
                },
            }
        )

        return validation_result

    def _discover_tools(self) -> None:
        """Discover tools from the MCP server and register their transport metadata."""
        # This is a simplified approach - in practice, you might need to
        # introspect the server's registered handlers
        self._registered_tools.clear()
        self._tool_handlers.clear()

        # Try to get tools from server if it has a method to list them
        if hasattr(self.mcp_server, "_tool_handlers"):
            self._tool_handlers.update(self.mcp_server._tool_handlers)

        # Extract tool names and register transport metadata from decorators
        for tool_name, handler in self._tool_handlers.items():
            # Create a basic Tool object - in practice this would come from the server
            self._registered_tools[tool_name] = Tool(
                name=tool_name, description=f"Tool: {tool_name}", inputSchema={"type": "object"}
            )

            # Register transport metadata if the handler has decorator attributes
            register_tool_with_server(self, handler)

    @asynccontextmanager
    async def run(self):
        """
        Run the multi-transport server.

        This context manager starts all registered transports and manages
        their lifecycle.
        """
        if self._is_running:
            raise RuntimeError("MultiTransportServer is already running")

        logger.info("Starting MultiTransportServer")

        try:
            # Discover tools from MCP server
            self._discover_tools()

            # Validate configuration if enabled
            if self.enable_routing_validation:
                validation = self.validate_configuration()
                if not validation["valid"]:
                    logger.error(f"Configuration validation failed: {validation['errors']}")
                    raise ValueError("Invalid multi-transport configuration")

                if validation["warnings"]:
                    for warning in validation["warnings"]:
                        logger.warning(warning)

            # Create task group for managing transports
            async with anyio.create_task_group() as tg:
                self._task_group = tg
                self._is_running = True

                # Start all registered transports
                await self.registry.start_all_transports()

                try:
                    yield self
                finally:
                    # Cleanup will happen in the finally block below
                    pass

        except Exception as e:
            logger.error(f"Failed to start MultiTransportServer: {e}")
            raise
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up server resources."""
        self._is_running = False

        # Stop all transports
        try:
            await self.registry.stop_all_transports()
        except Exception as e:
            logger.error(f"Error stopping transports: {e}")

        # Clear tool tracking
        self._registered_tools.clear()
        self._tool_handlers.clear()

        # Clear task group reference
        self._task_group = None

    def get_tool_routing_info(self) -> Dict[str, Dict[str, Any]]:
        """
        Get routing information for all tools.

        Returns:
            Dictionary mapping tool names to their routing information
        """
        routing_info = {}
        available_transports = list(self.registry.get_all_transports().keys())

        for tool_name in self._registered_tools.keys():
            try:
                assigned_transport = self.registry.make_routing_decision(tool_name, available_transports)

                tool_metadata = self.registry.get_tool_metadata(tool_name)

                routing_info[tool_name] = {
                    "assigned_transport": assigned_transport.value,
                    "has_explicit_transport": tool_metadata is not None,
                    "explicit_transport": tool_metadata.transport_type.value if tool_metadata else None,
                    "description": tool_metadata.description if tool_metadata else None,
                    "metadata": tool_metadata.metadata if tool_metadata else {},
                }

            except Exception as e:
                routing_info[tool_name] = {"error": str(e), "assigned_transport": None}

        return routing_info

    async def route_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> TransportType:
        """
        Route a tool call to the appropriate transport.

        Args:
            tool_name: Name of the tool being called
            arguments: Tool arguments

        Returns:
            The transport type that should handle this call
        """
        available_transports = list(self.registry.get_active_transports().keys())

        transport_type = self.registry.make_routing_decision(tool_name, available_transports)

        logger.debug(f"Routed tool '{tool_name}' to transport {transport_type}")

        return transport_type

    def get_tool_routing_map(self) -> Dict[str, str]:
        """
        Get a mapping of tool names to their assigned transport types.

        This can be used by clients to automatically route tool calls
        to the appropriate transport.

        Returns:
            Dictionary mapping tool names to transport type strings
        """
        routing_map = {}
        available_transports = list(self.registry.get_all_transports().keys())

        for tool_name in self._registered_tools.keys():
            try:
                transport_type = self.registry.make_routing_decision(tool_name, available_transports)
                routing_map[tool_name] = transport_type.value
            except Exception as e:
                logger.warning(f"Could not determine routing for tool {tool_name}: {e}")
                if self.registry.default_transport:
                    routing_map[tool_name] = self.registry.default_transport.value

        return routing_map
