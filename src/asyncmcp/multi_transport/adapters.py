"""
Transport adapters for integrating standard MCP transports with AsyncMCP multi-transport system.

This module provides adapters that wrap standard MCP transports (like HTTP) to work
with the AsyncMCP multi-transport architecture.
"""

import logging
from typing import Any, Optional, AsyncGenerator
from contextlib import asynccontextmanager

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.lowlevel import Server
from mcp.shared.message import SessionMessage
from mcp.server.session import ServerSession
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

# Import for Starlette HTTP server
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response

from asyncmcp.common.protocols import ServerTransportProtocol
from .registry import TransportManager

logger = logging.getLogger(__name__)


class HttpTransportAdapter(TransportManager):
    """
    Adapter to integrate modern StreamableHTTP transport with multi-transport system.
    
    This adapter wraps the MCP StreamableHTTP transport to work within
    the AsyncMCP multi-transport architecture.
    """
    
    def __init__(
        self,
        mcp_server: Server,
        host: str = "localhost",
        port: int = 8000,
        path: str = "/mcp",
        stateless: bool = False,
        json_response: bool = False,
        security_settings: Optional[TransportSecuritySettings] = None
    ):
        self.mcp_server = mcp_server
        self.host = host
        self.port = port
        self.path = path
        self.stateless = stateless
        self.json_response = json_response
        self.security_settings = security_settings
        
        self._session_manager: Optional[StreamableHTTPSessionManager] = None
        self._is_running = False
        self._context_manager = None
        self._http_server = None
        self._server_task = None
        
    @property
    def is_running(self) -> bool:
        """Check if the HTTP transport is running."""
        return self._is_running
    
    async def start(self) -> None:
        """Start the StreamableHTTP transport server."""
        if self._is_running:
            logger.warning("HTTP transport is already running")
            return
        
        try:
            # Create StreamableHTTP session manager
            self._session_manager = StreamableHTTPSessionManager(
                app=self.mcp_server,
                stateless=self.stateless,
                json_response=self.json_response,
                security_settings=self.security_settings
            )
            
            # Create task group for the session manager and server
            self._server_task = anyio.create_task_group()
            await self._server_task.__aenter__()
            
            # Initialize session manager with its own task group
            self._context_manager = self._session_manager.run()
            await self._context_manager.__aenter__()
            
            # Create ASGI app that handles MCP requests
            async def asgi_app(scope, receive, send):
                await self._session_manager.handle_request(scope, receive, send)
            
            # Create Starlette app with the MCP handler
            app = Starlette()
            app.routes.append(Mount(self.path, asgi_app))
            
            # Start the HTTP server with uvicorn
            config = uvicorn.Config(
                app=app,
                host=self.host,
                port=self.port,
                log_level="warning"  # Reduce log noise
            )
            self._http_server = uvicorn.Server(config)
            
            # Start server in background task
            async def _run_server():
                await self._http_server.serve()
            
            self._server_task.start_soon(_run_server)
            
            self._is_running = True
            logger.info(f"StreamableHTTP transport started on http://{self.host}:{self.port}{self.path}")
            
        except Exception as e:
            logger.error(f"Failed to start HTTP transport: {e}")
            await self._cleanup()
            raise
    
    async def stop(self) -> None:
        """Stop the StreamableHTTP transport server."""
        if not self._is_running:
            logger.warning("HTTP transport is not running")
            return
        
        try:
            # Stop the HTTP server
            if self._http_server:
                self._http_server.should_exit = True
                
            # Stop the session manager context
            if self._context_manager:
                try:
                    await self._context_manager.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error exiting session manager context: {e}")
                finally:
                    self._context_manager = None
                
            # Stop the server task group
            if self._server_task:
                try:
                    self._server_task.cancel_scope.cancel()
                    await self._server_task.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error exiting task group: {e}")
                finally:
                    self._server_task = None
            
            self._http_server = None
            self._is_running = False
            logger.info("StreamableHTTP transport stopped")
            
        except Exception as e:
            logger.error(f"Error stopping StreamableHTTP transport: {e}")
            self._is_running = False
    
    async def _cleanup(self) -> None:
        """Clean up transport resources."""
        try:
            if self._http_server:
                self._http_server.should_exit = True
                
            # Cleanup session manager context
            if self._context_manager:
                try:
                    await self._context_manager.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error exiting session manager context during cleanup: {e}")
                finally:
                    self._context_manager = None
                
            if self._server_task:
                try:
                    self._server_task.cancel_scope.cancel()
                    await self._server_task.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error exiting task group during cleanup: {e}")
                finally:
                    self._server_task = None
            
            self._http_server = None
            self._session_manager = None
                
        except Exception as e:
            logger.debug(f"Error during StreamableHTTP transport cleanup: {e}")
        finally:
            self._is_running = False


# StdioTransportAdapter commented out due to import issues with current MCP version
# class StdioTransportAdapter(TransportManager):
#     """STDIO transport adapter - not commonly used in multi-transport scenarios"""
#     pass


class AsyncMcpTransportAdapter(ServerTransportProtocol):
    """
    Adapter to make AsyncMCP transports compatible with the multi-transport system.
    
    This adapter wraps existing AsyncMCP transports (SQS, SNS+SQS, Webhook) to
    provide a consistent interface for the multi-transport registry.
    """
    
    def __init__(self, transport_instance: Any, session_id: Optional[str] = None):
        self.transport_instance = transport_instance
        self.session_id = session_id
        self._terminated = False
    
    @property
    def is_terminated(self) -> bool:
        """Check if the transport is terminated."""
        return self._terminated
    
    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[tuple[MemoryObjectReceiveStream, MemoryObjectSendStream], None]:
        """Connect using the wrapped transport."""
        if hasattr(self.transport_instance, 'connect'):
            async with self.transport_instance.connect() as streams:
                yield streams
        else:
            raise NotImplementedError(f"Transport {type(self.transport_instance)} does not support connect()")
    
    async def terminate(self) -> None:
        """Terminate the wrapped transport."""
        if self._terminated:
            return
        
        self._terminated = True
        
        if hasattr(self.transport_instance, 'terminate'):
            await self.transport_instance.terminate()
        elif hasattr(self.transport_instance, 'cleanup'):
            await self.transport_instance.cleanup()
    
    async def cleanup(self) -> None:
        """Clean up the wrapped transport."""
        if hasattr(self.transport_instance, 'cleanup'):
            await self.transport_instance.cleanup()


class WebhookTransportAdapter(TransportManager):
    """
    Adapter to integrate AsyncMCP webhook transport with multi-transport system.
    
    This adapter wraps the AsyncMCP WebhookSessionManager to work as a
    TransportManager in the multi-transport registry.
    """
    
    def __init__(self, webhook_manager: Any):
        self.webhook_manager = webhook_manager
        self._is_running = False
        self._context_manager = None
    
    @property
    def is_running(self) -> bool:
        """Check if the webhook transport is running."""
        return self._is_running
    
    async def start(self) -> None:
        """Start the webhook transport."""
        if self._is_running:
            logger.warning("Webhook transport is already running")
            return
        
        try:
            # Start the webhook manager
            self._context_manager = self.webhook_manager.run()
            await self._context_manager.__aenter__()
            
            self._is_running = True
            logger.info("Webhook transport started")
            
        except Exception as e:
            logger.error(f"Failed to start webhook transport: {e}")
            self._is_running = False
            raise
    
    async def stop(self) -> None:
        """Stop the webhook transport."""
        if not self._is_running:
            logger.warning("Webhook transport is not running")
            return
        
        try:
            if self._context_manager:
                try:
                    await self._context_manager.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error exiting webhook context manager: {e}")
                finally:
                    self._context_manager = None
            
            self._is_running = False
            logger.info("Webhook transport stopped")
            
        except Exception as e:
            logger.error(f"Error stopping webhook transport: {e}")
            self._is_running = False