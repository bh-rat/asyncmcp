"""
Transport registry for managing multiple transport types in AsyncMCP.

This module provides the TransportRegistry class that manages multiple transport
instances and routes messages based on transport type and tool associations.
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, List, Set, Protocol, runtime_checkable
from dataclasses import dataclass

from mcp.server.lowlevel import Server
from asyncmcp.common.protocols import ServerTransportProtocol

logger = logging.getLogger(__name__)


class TransportType(str, Enum):
    """Supported transport types."""
    HTTP = "http"
    WEBHOOK = "webhook"
    SQS = "sqs"
    SNS_SQS = "sns_sqs"


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
        self._default_transport: Optional[TransportType] = None
        
    def register_transport(
        self,
        transport_type: TransportType,
        transport_instance: Any,
        config: Any,
        tools: Optional[List[str]] = None,
        is_default: bool = False
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
            transport_type=transport_type,
            transport_instance=transport_instance,
            config=config,
            tools=tool_set
        )
        
        self._transports[transport_type] = transport_info
        
        # Update tool-transport mapping
        for tool_name in tool_set:
            if tool_name in self._tool_transport_mapping:
                logger.warning(f"Tool '{tool_name}' is already mapped to transport {self._tool_transport_mapping[tool_name]}, overriding with {transport_type}")
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
        
        transport_info = self._transports[transport_type]
        
        # Remove tool mappings
        tools_to_remove = [tool for tool, t_type in self._tool_transport_mapping.items() 
                          if t_type == transport_type]
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
                logger.warning(f"Tool '{tool_name}' is already mapped to transport {self._tool_transport_mapping[tool_name]}, overriding with {transport_type}")
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