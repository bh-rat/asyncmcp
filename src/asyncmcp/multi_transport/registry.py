"""
Transport registry for managing multiple transport types in AsyncMCP.

This module provides the TransportRegistry class that manages multiple transport
instances and routes messages based on transport type and tool associations.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Set, runtime_checkable

from mcp.server.lowlevel import Server

logger = logging.getLogger(__name__)


class TransportType(str, Enum):
    """Supported transport types."""

    HTTP = "http"
    WEBHOOK = "webhook"
    SQS = "sqs"
    SNS_SQS = "sns_sqs"


@dataclass
class ToolTransportInfo:
    """Metadata about a tool's transport requirements."""

    tool_name: str
    transport_type: TransportType
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TransportInfo:
    """Information about a registered transport."""

    transport_type: TransportType
    transport_instance: Any  # Transport manager or server instance
    config: Any
    tools: Set[str]  # Tool names associated with this transport
    is_active: bool = False


@runtime_checkable
class TransportManager(Protocol):
    """Protocol for transport managers that can be registered."""

    async def start(self) -> None:
        """Start the transport manager."""
        ...

    async def stop(self) -> None:
        """Stop the transport manager."""
        ...

    @property
    def is_running(self) -> bool:
        """Check if the transport manager is running."""
        ...


class TransportRegistry:
    """Registry for managing multiple transport types."""

    def __init__(self, mcp_server: Server):
        self.mcp_server = mcp_server
        self._transports: Dict[TransportType, TransportInfo] = {}
        self._tool_transport_mapping: Dict[str, TransportType] = {}
        self._tool_metadata: Dict[str, ToolTransportInfo] = {}
        self._default_transport: Optional[TransportType] = None

    def register_transport(
        self,
        transport_type: TransportType,
        transport_instance: Any,
        config: Any,
        tools: Optional[List[str]] = None,
        is_default: bool = False,
    ) -> None:
        """
        Register a transport type with the registry.

        Args:
            transport_type: The type of transport to register
            transport_instance: The transport manager or server instance
            config: Transport-specific configuration
            tools: List of tool names that should use this transport
            is_default: Whether this should be the default transport
        """
        if transport_type in self._transports:
            raise ValueError(f"Transport type {transport_type} is already registered")

        # Validate that the transport instance has the required interface
        if not isinstance(transport_instance, TransportManager):
            logger.warning(f"Transport instance for {transport_type} does not implement TransportManager protocol")

        tool_set = set(tools) if tools else set()

        transport_info = TransportInfo(
            transport_type=transport_type, transport_instance=transport_instance, config=config, tools=tool_set
        )

        self._transports[transport_type] = transport_info

        # Update tool-transport mapping
        for tool_name in tool_set:
            if tool_name in self._tool_transport_mapping:
                existing_transport = self._tool_transport_mapping[tool_name]
                logger.warning(
                    f"Tool '{tool_name}' is already mapped to transport {existing_transport}, "
                    f"overriding with {transport_type}"
                )
            self._tool_transport_mapping[tool_name] = transport_type

        # Set as default if requested or if it's the first transport
        if is_default or self._default_transport is None:
            self._default_transport = transport_type

        logger.info(f"Registered transport {transport_type} with tools: {tool_set}")

    def unregister_transport(self, transport_type: TransportType) -> None:
        """
        Unregister a transport type from the registry.

        Args:
            transport_type: The transport type to unregister
        """
        if transport_type not in self._transports:
            logger.warning(f"Transport type {transport_type} is not registered")
            return

        # Remove tool mappings
        tools_to_remove = [tool for tool, t_type in self._tool_transport_mapping.items() if t_type == transport_type]
        for tool_name in tools_to_remove:
            del self._tool_transport_mapping[tool_name]

        # Remove transport
        del self._transports[transport_type]

        # Update default transport if needed
        if self._default_transport == transport_type:
            self._default_transport = next(iter(self._transports.keys())) if self._transports else None

        logger.info(f"Unregistered transport {transport_type}")

    def get_transport_for_tool(self, tool_name: str) -> Optional[TransportType]:
        """
        Get the transport type that should handle a specific tool.

        Args:
            tool_name: The name of the tool

        Returns:
            The transport type that should handle the tool, or None if not found
        """
        return self._tool_transport_mapping.get(tool_name, self._default_transport)

    def get_transport_info(self, transport_type: TransportType) -> Optional[TransportInfo]:
        """
        Get information about a registered transport.

        Args:
            transport_type: The transport type to get info for

        Returns:
            TransportInfo if the transport is registered, None otherwise
        """
        return self._transports.get(transport_type)

    def get_all_transports(self) -> Dict[TransportType, TransportInfo]:
        """Get all registered transports."""
        return self._transports.copy()

    def get_active_transports(self) -> Dict[TransportType, TransportInfo]:
        """Get all active (running) transports."""
        return {t_type: info for t_type, info in self._transports.items() if info.is_active}

    def set_transport_tools(self, transport_type: TransportType, tools: List[str]) -> None:
        """
        Update the tools associated with a transport.

        Args:
            transport_type: The transport type to update
            tools: New list of tool names for this transport
        """
        if transport_type not in self._transports:
            raise ValueError(f"Transport type {transport_type} is not registered")

        transport_info = self._transports[transport_type]

        # Remove old tool mappings for this transport
        old_tools = transport_info.tools.copy()
        for tool_name in old_tools:
            if self._tool_transport_mapping.get(tool_name) == transport_type:
                del self._tool_transport_mapping[tool_name]

        # Add new tool mappings
        new_tool_set = set(tools)
        transport_info.tools = new_tool_set

        for tool_name in new_tool_set:
            if tool_name in self._tool_transport_mapping:
                existing_transport = self._tool_transport_mapping[tool_name]
                logger.warning(
                    f"Tool '{tool_name}' is already mapped to transport {existing_transport}, "
                    f"overriding with {transport_type}"
                )
            self._tool_transport_mapping[tool_name] = transport_type

        logger.info(f"Updated tools for transport {transport_type}: {new_tool_set}")

    async def start_transport(self, transport_type: TransportType) -> None:
        """
        Start a specific transport.

        Args:
            transport_type: The transport type to start
        """
        if transport_type not in self._transports:
            raise ValueError(f"Transport type {transport_type} is not registered")

        transport_info = self._transports[transport_type]

        if transport_info.is_active:
            logger.warning(f"Transport {transport_type} is already active")
            return

        try:
            if isinstance(transport_info.transport_instance, TransportManager):
                await transport_info.transport_instance.start()
            transport_info.is_active = True
            logger.info(f"Started transport {transport_type}")
        except Exception as e:
            logger.error(f"Failed to start transport {transport_type}: {e}")
            raise

    async def stop_transport(self, transport_type: TransportType) -> None:
        """
        Stop a specific transport.

        Args:
            transport_type: The transport type to stop
        """
        if transport_type not in self._transports:
            logger.warning(f"Transport type {transport_type} is not registered")
            return

        transport_info = self._transports[transport_type]

        if not transport_info.is_active:
            logger.warning(f"Transport {transport_type} is not active")
            return

        try:
            if isinstance(transport_info.transport_instance, TransportManager):
                await transport_info.transport_instance.stop()
            transport_info.is_active = False
            logger.info(f"Stopped transport {transport_type}")
        except Exception as e:
            logger.error(f"Failed to stop transport {transport_type}: {e}")
            raise

    async def start_all_transports(self) -> None:
        """Start all registered transports."""
        for transport_type in self._transports:
            try:
                await self.start_transport(transport_type)
            except Exception as e:
                logger.error(f"Failed to start transport {transport_type}: {e}")
                # Continue starting other transports

    async def stop_all_transports(self) -> None:
        """Stop all active transports."""
        for transport_type in list(self._transports.keys()):
            try:
                await self.stop_transport(transport_type)
            except Exception as e:
                logger.error(f"Failed to stop transport {transport_type}: {e}")
                # Continue stopping other transports

    def get_tool_transport_mapping(self) -> Dict[str, TransportType]:
        """Get the current tool-to-transport mapping."""
        return self._tool_transport_mapping.copy()

    @property
    def default_transport(self) -> Optional[TransportType]:
        """Get the default transport type."""
        return self._default_transport

    @default_transport.setter
    def default_transport(self, transport_type: Optional[TransportType]) -> None:
        """Set the default transport type."""
        if transport_type is not None and transport_type not in self._transports:
            raise ValueError(f"Transport type {transport_type} is not registered")
        self._default_transport = transport_type
        logger.info(f"Set default transport to {transport_type}")

    def register_tool_metadata(
        self, tool_name: str, transport_type: TransportType, description: Optional[str] = None, **metadata: Any
    ) -> None:
        """
        Register transport metadata for a tool.

        Args:
            tool_name: The name of the tool
            transport_type: The transport type this tool should use
            description: Optional description of why this transport is used
            **metadata: Additional metadata for the tool-transport association
        """
        self._tool_metadata[tool_name] = ToolTransportInfo(
            tool_name=tool_name, transport_type=transport_type, description=description, metadata=metadata
        )

        # Also update the tool-transport mapping
        self._tool_transport_mapping[tool_name] = transport_type

        logger.debug(f"Registered metadata for tool '{tool_name}': {transport_type}")

    def get_tool_metadata(self, tool_name: str) -> Optional[ToolTransportInfo]:
        """Get transport metadata for a specific tool."""
        return self._tool_metadata.get(tool_name)

    def get_all_tool_metadata(self) -> Dict[str, ToolTransportInfo]:
        """Get all registered tool transport metadata."""
        return self._tool_metadata.copy()

    def get_tools_for_transport(self, transport_type: TransportType) -> List[str]:
        """Get all tool names associated with a specific transport type."""
        return [info.tool_name for info in self._tool_metadata.values() if info.transport_type == transport_type]

    def unregister_tool_metadata(self, tool_name: str) -> bool:
        """
        Remove transport metadata for a specific tool.

        Args:
            tool_name: The name of the tool to unregister

        Returns:
            True if the tool was found and removed, False otherwise
        """
        if tool_name in self._tool_metadata:
            del self._tool_metadata[tool_name]
            # Also remove from tool-transport mapping if it exists
            self._tool_transport_mapping.pop(tool_name, None)
            logger.debug(f"Unregistered metadata for tool '{tool_name}'")
            return True
        return False

    def make_routing_decision(
        self, tool_name: str, available_transports: Optional[List[TransportType]] = None
    ) -> TransportType:
        """
        Make a routing decision for a tool call.

        Args:
            tool_name: The name of the tool being called
            available_transports: List of available transport types (defaults to active transports)

        Returns:
            The transport type that should handle this tool call

        Raises:
            ValueError: If no suitable transport is found
        """
        if available_transports is None:
            available_transports = list(self.get_active_transports().keys())

        # Check if the tool has specific transport requirements
        tool_metadata = self.get_tool_metadata(tool_name)

        if tool_metadata and tool_metadata.transport_type in available_transports:
            logger.debug(f"Routing tool '{tool_name}' to transport {tool_metadata.transport_type} (explicit)")
            return tool_metadata.transport_type

        # Fall back to default transport
        if self._default_transport and self._default_transport in available_transports:
            logger.debug(f"Routing tool '{tool_name}' to default transport {self._default_transport}")
            return self._default_transport

        # Use the first available transport as last resort
        if available_transports:
            transport = available_transports[0]
            logger.debug(f"Routing tool '{tool_name}' to first available transport {transport}")
            return transport

        raise ValueError(f"No suitable transport found for tool '{tool_name}'. Available: {available_transports}")

    def validate_tool_routing(self, tool_names: List[str]) -> Dict[str, Any]:
        """
        Validate that all tools can be routed to available transports.

        Args:
            tool_names: List of tool names to validate

        Returns:
            Dictionary with validation results and warnings
        """
        results = {"valid": True, "warnings": [], "errors": [], "tool_routing": {}}
        available_transports = list(self.get_active_transports().keys())

        for tool_name in tool_names:
            try:
                transport = self.make_routing_decision(tool_name, available_transports)
                results["tool_routing"][tool_name] = transport

                # Check if tool has specific requirements that aren't being met
                tool_metadata = self.get_tool_metadata(tool_name)
                if tool_metadata and tool_metadata.transport_type != transport:
                    results["warnings"].append(
                        f"Tool '{tool_name}' prefers {tool_metadata.transport_type} but will use {transport}"
                    )

            except ValueError as e:
                results["valid"] = False
                results["errors"].append(f"Tool '{tool_name}': {str(e)}")

        return results
