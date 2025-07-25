"""
Routing and metadata system for multi-transport AsyncMCP.

This module provides decorators and utilities for associating tools with
specific transport types and managing routing metadata.
"""

import logging
from typing import Dict, Any, Optional, Callable, List
from dataclasses import dataclass, field
from functools import wraps

from .registry import TransportType

logger = logging.getLogger(__name__)


@dataclass
class ToolTransportInfo:
    """Metadata about a tool's transport requirements."""
    tool_name: str
    transport_type: TransportType
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# Global registry for tool transport metadata
_TOOL_TRANSPORT_METADATA: Dict[str, ToolTransportInfo] = {}


def transport_info(
    transport_type: TransportType,
    description: Optional[str] = None,
    **metadata: Any
) -> Callable:
    """
    Decorator to associate a tool with a specific transport type.
    
    Args:
        transport_type: The transport type this tool should use
        description: Optional description of why this transport is used
        **metadata: Additional metadata for the tool-transport association
        
    Example:
        @transport_info(TransportType.WEBHOOK, description="Requires webhook for async responses")
        @app.call_tool()
        async def long_running_tool(name: str, arguments: dict):
            # Tool implementation
            pass
    """
    def decorator(func: Callable) -> Callable:
        # Extract tool name from the function
        tool_name = getattr(func, '__name__', 'unknown')
        
        # Store metadata
        _TOOL_TRANSPORT_METADATA[tool_name] = ToolTransportInfo(
            tool_name=tool_name,
            transport_type=transport_type,
            description=description,
            metadata=metadata
        )
        
        logger.debug(f"Registered transport info for tool '{tool_name}': {transport_type}")
        
        return func
    
    return decorator


def get_tool_transport_info(tool_name: str) -> Optional[ToolTransportInfo]:
    """
    Get transport information for a specific tool.
    
    Args:
        tool_name: The name of the tool
        
    Returns:
        ToolTransportInfo if the tool has transport metadata, None otherwise
    """
    return _TOOL_TRANSPORT_METADATA.get(tool_name)


def get_all_tool_transport_info() -> Dict[str, ToolTransportInfo]:
    """Get all registered tool transport metadata."""
    return _TOOL_TRANSPORT_METADATA.copy()


def get_tools_for_transport(transport_type: TransportType) -> List[str]:
    """
    Get all tool names associated with a specific transport type.
    
    Args:
        transport_type: The transport type to filter by
        
    Returns:
        List of tool names that use the specified transport type
    """
    return [
        info.tool_name for info in _TOOL_TRANSPORT_METADATA.values()
        if info.transport_type == transport_type
    ]


def clear_tool_transport_metadata() -> None:
    """Clear all tool transport metadata. Useful for testing."""
    global _TOOL_TRANSPORT_METADATA
    _TOOL_TRANSPORT_METADATA.clear()


def register_tool_transport_info(
    tool_name: str,
    transport_type: TransportType,
    description: Optional[str] = None,
    **metadata: Any
) -> None:
    """
    Programmatically register transport info for a tool.
    
    Args:
        tool_name: The name of the tool
        transport_type: The transport type this tool should use
        description: Optional description of why this transport is used
        **metadata: Additional metadata for the tool-transport association
    """
    _TOOL_TRANSPORT_METADATA[tool_name] = ToolTransportInfo(
        tool_name=tool_name,
        transport_type=transport_type,
        description=description,
        metadata=metadata
    )
    
    logger.debug(f"Programmatically registered transport info for tool '{tool_name}': {transport_type}")


def unregister_tool_transport_info(tool_name: str) -> bool:
    """
    Remove transport info for a specific tool.
    
    Args:
        tool_name: The name of the tool to unregister
        
    Returns:
        True if the tool was found and removed, False otherwise
    """
    if tool_name in _TOOL_TRANSPORT_METADATA:
        del _TOOL_TRANSPORT_METADATA[tool_name]
        logger.debug(f"Unregistered transport info for tool '{tool_name}'")
        return True
    return False


class RoutingContext:
    """Context manager for routing decisions and metadata."""
    
    def __init__(self):
        self._current_tool: Optional[str] = None
        self._current_transport: Optional[TransportType] = None
        self._routing_metadata: Dict[str, Any] = {}
    
    def set_current_context(
        self,
        tool_name: Optional[str] = None,
        transport_type: Optional[TransportType] = None,
        **metadata: Any
    ) -> None:
        """Set the current routing context."""
        self._current_tool = tool_name
        self._current_transport = transport_type
        self._routing_metadata.update(metadata)
    
    def clear_context(self) -> None:
        """Clear the current routing context."""
        self._current_tool = None
        self._current_transport = None
        self._routing_metadata.clear()
    
    @property
    def current_tool(self) -> Optional[str]:
        """Get the current tool name."""
        return self._current_tool
    
    @property
    def current_transport(self) -> Optional[TransportType]:
        """Get the current transport type."""
        return self._current_transport
    
    @property
    def routing_metadata(self) -> Dict[str, Any]:
        """Get the current routing metadata."""
        return self._routing_metadata.copy()


# Global routing context instance
routing_context = RoutingContext()


def get_routing_decision(
    tool_name: str,
    available_transports: List[TransportType],
    default_transport: Optional[TransportType] = None
) -> TransportType:
    """
    Make a routing decision for a tool call.
    
    Args:
        tool_name: The name of the tool being called
        available_transports: List of available transport types
        default_transport: Default transport to use if no specific routing found
        
    Returns:
        The transport type that should handle this tool call
        
    Raises:
        ValueError: If no suitable transport is found
    """
    # Check if the tool has specific transport requirements
    tool_info = get_tool_transport_info(tool_name)
    
    if tool_info and tool_info.transport_type in available_transports:
        logger.debug(f"Routing tool '{tool_name}' to transport {tool_info.transport_type} (explicit)")
        return tool_info.transport_type
    
    # Fall back to default transport
    if default_transport and default_transport in available_transports:
        logger.debug(f"Routing tool '{tool_name}' to default transport {default_transport}")
        return default_transport
    
    # Use the first available transport as last resort
    if available_transports:
        transport = available_transports[0]
        logger.debug(f"Routing tool '{tool_name}' to first available transport {transport}")
        return transport
    
    raise ValueError(f"No suitable transport found for tool '{tool_name}'. Available: {available_transports}")


def validate_routing_config(
    tool_names: List[str],
    available_transports: List[TransportType],
    default_transport: Optional[TransportType] = None
) -> Dict[str, Any]:
    """
    Validate that all tools can be routed to available transports.
    
    Args:
        tool_names: List of tool names to validate
        available_transports: List of available transport types
        default_transport: Default transport type
        
    Returns:
        Dictionary with validation results and warnings
    """
    results = {
        "valid": True,
        "warnings": [],
        "errors": [],
        "tool_routing": {}
    }
    
    for tool_name in tool_names:
        try:
            transport = get_routing_decision(tool_name, available_transports, default_transport)
            results["tool_routing"][tool_name] = transport
            
            # Check if tool has specific requirements that aren't being met
            tool_info = get_tool_transport_info(tool_name)
            if tool_info and tool_info.transport_type != transport:
                results["warnings"].append(
                    f"Tool '{tool_name}' prefers {tool_info.transport_type} but will use {transport}"
                )
                
        except ValueError as e:
            results["valid"] = False
            results["errors"].append(f"Tool '{tool_name}': {str(e)}")
    
    return results