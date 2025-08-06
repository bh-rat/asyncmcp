"""Session resolution logic for proxy server.

This module handles the complex logic of resolving session IDs from
various sources including headers, cookies, and request context.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from starlette.requests import Request

logger = logging.getLogger(__name__)


class SessionManagerProtocol(Protocol):
    """Protocol for session manager."""

    @property
    def active_session_count(self) -> int:
        """Get the number of active sessions."""
        ...

    def create_session_id(self) -> str:
        """Create a new session ID."""
        ...

    def get_single_session_id(self) -> Optional[str]:
        """Get the single session ID if only one exists."""
        ...

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists."""
        ...


@dataclass
class SessionResolution:
    """Result of session resolution."""

    session_id: Optional[str]
    is_new_session: bool
    error_message: Optional[str] = None


class SessionResolver:
    """Handles session resolution from various sources.

    This class encapsulates the complex logic of determining which
    session to use based on headers, cookies, initialization state,
    and configuration.
    """

    def __init__(self, session_manager: SessionManagerProtocol, stateless: bool = False):
        """Initialize the session resolver.

        Args:
            session_manager: The session manager to use
            stateless: Whether to run in stateless mode
        """
        self.session_manager = session_manager
        self.stateless = stateless

    async def resolve_session(
        self,
        request: Request,
        message_data: Dict[str, Any],
    ) -> SessionResolution:
        """Resolve the session ID from various sources.

        Args:
            request: The HTTP request
            message_data: The parsed message data
            debug_logging: Whether to log debug information

        Returns:
            SessionResolution with the session ID and metadata
        """
        is_initialize = message_data.get("method") == "initialize"

        # 1. Check X-Session-Id header (for clients that support it)
        session_id = request.headers.get("X-Session-Id")
        if session_id:
            logger.debug(f"Found session ID in X-Session-Id header: {session_id}")
            return SessionResolution(session_id, False)

        # 2. Check cookies for session (for browser-based clients like MCP Inspector)
        if "cookie" in request.headers:
            cookies = request.cookies
            session_id = cookies.get("mcp-session-id")
            if session_id:
                logger.debug(f"Found session ID in cookie: {session_id}")
                return SessionResolution(session_id, False)

        # 3. For stateless mode or single session, use a default session
        if self.stateless or is_initialize:
            # In stateless mode or for initialize, use/create a default session
            if self.stateless:
                session_id = "default"
                logger.debug("Using default session for stateless mode")
                return SessionResolution(session_id, False)
            elif is_initialize:
                # For initialize, try to use existing session if there's only one
                if self.session_manager.active_session_count == 1:
                    session_id = self.session_manager.get_single_session_id()
                    logger.info(f"Using existing single session for initialize: {session_id}")
                    return SessionResolution(session_id, False)
                else:
                    # Create new session for initialize requests
                    session_id = self.session_manager.create_session_id()
                    logger.info(f"New session created for initialize request: {session_id}")
                    return SessionResolution(session_id, True)

        # 4. If still no session ID and not initialize, try to get the first/only active session
        # This supports MCP Inspector which expects a single session per server
        if self.session_manager.active_session_count == 1:
            # Get the single active session
            session_id = self.session_manager.get_single_session_id()
            if session_id:
                return SessionResolution(session_id, False)

        # 5. Final check - if no session ID found, return error
        logger.error(f"No session ID found. Active sessions: {self.session_manager.active_session_count}")
        return SessionResolution(None, False, "No active session. Please initialize first.")

    def resolve_sse_session(self, request: Request) -> tuple[str, bool]:
        """Resolve session for SSE connections.

        Args:
            request: The HTTP request

        Returns:
            Tuple of (session_id, is_new_session)
        """
        # Check for existing session ID
        session_id = request.headers.get("X-Session-Id")
        if not session_id:
            # Create new session for SSE
            session_id = self.session_manager.create_session_id()
            client_host = request.client.host if request.client else "unknown"
            logger.info(f"New SSE connection from {client_host}, assigned session {session_id}")
            return session_id, True
        else:
            client_host = request.client.host if request.client else "unknown"
            logger.info(f"Existing SSE connection from {client_host}, session {session_id}")
            return session_id, False
