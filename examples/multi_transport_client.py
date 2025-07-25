#!/usr/bin/env python3
"""
Multi-Transport MCP Client supporting both StreamableHTTP and Webhook transports.

This client demonstrates:
- StreamableHTTP client for synchronous tool calls (immediate response)
- Webhook client for asynchronous tool calls (webhook callback response)
- Interactive CLI for testing both transports
- Complete multi-transport client architecture
"""

import sys
import time

import anyio
import click
from typing import Dict, Any, Optional

import mcp.types as types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage

from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.utils import WebhookClientConfig
from shared import print_colored, print_json, send_mcp_request, DEFAULT_INIT_PARAMS

# Global flag to track initialization
_init_complete = False


class MultiTransportClient:
    """
    Interactive multi-transport client that can communicate with both HTTP and webhook transports.
    """
    
    def __init__(
        self,
        http_server_url: str,
        webhook_server_url: str,
        webhook_client_port: int
    ):
        self.http_server_url = http_server_url
        self.webhook_server_url = webhook_server_url
        self.webhook_client_port = webhook_client_port
        
        # Transport configurations
        self.webhook_config = WebhookClientConfig(
            server_url=webhook_server_url,
            client_id="multi-transport-client"
        )
        self.webhook_url = f"http://localhost:{webhook_client_port}/webhook/response"
        
        # Available tools and current transport
        self.available_tools: Dict[str, types.Tool] = {}
        self.current_transport = "http"  # Default to HTTP
        self.http_read = None
        self.http_write = None
        self.webhook_read = None
        self.webhook_write = None
    
    async def handle_response(self, message):
        """Handle responses from the server."""
        global _init_complete
        
        if hasattr(message, "error"):
            error = message.error
            print_colored(f"❌ Error: {error}", "red")
            return

        if not hasattr(message, "result") or not isinstance(message.result, dict):
            return

        result = message.result

        if "serverInfo" in result:
            server_info = result["serverInfo"]
            print_colored(f"✅ Initialized with server: {server_info.get('name', 'Unknown')}", "green")
            _init_complete = True
            return

        if "tools" in result:
            tools = result["tools"]
            print_colored(f"✅ Found {len(tools)} tools:", "green")
            self.available_tools.clear()
            for tool in tools:
                tool_name = tool.get('name', 'Unknown')
                self.available_tools[tool_name] = tool
                print_colored(f"   • {tool_name}: {tool.get('description', 'No description')}", "white")
            return

        if "content" in result:
            content = result["content"]
            print_colored("✅ Tool result:", "green")
            for item in content:
                if item.get("type") == "text":
                    text = item.get('text', '')
                    if len(text) > 2000:
                        print_colored(f"   📄 {text[:2000]}...\n   [Content truncated]", "white")
                    else:
                        print_colored(f"   📄 {text}", "white")
                else:
                    print_json(item, "Content Item")
            return

        # Default case for other dict results
        print_colored("✅ Response received:", "green")
        print_json(result)
    
    async def send_request(self, write_stream, method: str, params: dict = None):
        """Send a request using the current transport."""
        request_id = int(time.time() * 1000) % 100000
        await send_mcp_request(write_stream, method, params, request_id)
    
    async def send_initialized_notification(self, write_stream):
        """Send initialized notification."""
        notification = types.JSONRPCMessage.model_validate(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        session_message = SessionMessage(notification)
        await write_stream.send(session_message)
        print_colored("📤 Sent initialized notification", "cyan")
    
    async def process_command(self, command: str):
        """Process a single command."""
        parts = command.split()
        
        if not parts:
            return True
        
        cmd = parts[0].lower()
        
        if cmd in ["quit", "exit", "q"]:
            print_colored("👋 Goodbye!", "yellow")
            return False
        
        elif cmd == "transport":
            if len(parts) >= 2:
                new_transport = parts[1].lower()
                if new_transport in ["http", "webhook"]:
                    self.current_transport = new_transport
                    print_colored(f"🔄 Switched to {new_transport.upper()} transport", "cyan")
                else:
                    print_colored("❌ Valid transports: http, webhook", "red")
            else:
                print_colored(f"📡 Current transport: {self.current_transport.upper()}", "blue")
        
        elif cmd == "init":
            global _init_complete
            _init_complete = False
            
            write_stream = self.http_write if self.current_transport == "http" else self.webhook_write
            if not write_stream:
                print_colored("❌ Transport not connected", "red")
                return True
            
            await self.send_request(write_stream, "initialize", DEFAULT_INIT_PARAMS)
            
            # Wait for initialize response
            print_colored("⏳ Waiting for initialize response...", "yellow")
            try:
                with anyio.move_on_after(30):
                    while not _init_complete:
                        await anyio.sleep(0.1)
            except Exception as e:
                print_colored(f"❌ Timeout waiting for initialize response: {e}", "red")
                return True
            
            await self.send_initialized_notification(write_stream)
            await anyio.sleep(0.5)
        
        elif cmd == "tools":
            write_stream = self.http_write if self.current_transport == "http" else self.webhook_write
            if not write_stream:
                print_colored("❌ Transport not connected", "red")
                return True
            
            await self.send_request(write_stream, "tools/list", {})
        
        elif cmd == "call":
            if len(parts) >= 2:
                tool_name = parts[1]
                param_parts = parts[2:]
                arguments = {}
                
                for param in param_parts:
                    if "=" in param:
                        key, value = param.split("=", 1)
                        arguments[key] = value
                    else:
                        param_index = len([k for k in arguments.keys() if k.startswith("param")])
                        arguments[f"param{param_index}"] = param
                
                write_stream = self.http_write if self.current_transport == "http" else self.webhook_write
                if not write_stream:
                    print_colored("❌ Transport not connected", "red")
                    return True
                
                params = {"name": tool_name, "arguments": arguments}
                await self.send_request(write_stream, "tools/call", params)
            else:
                print_colored("❌ Usage: call <tool_name> <params...>", "red")
        
        elif cmd == "help":
            print_colored("📖 Available commands:", "cyan")
            print_colored("   init                    - Initialize connection", "white")
            print_colored("   tools                   - List available tools", "white")
            print_colored("   call <tool> <params>    - Call a tool", "white")
            print_colored("   transport [http|webhook] - Switch/show transport", "white")
            print_colored("   help                    - Show this help", "white")
            print_colored("   quit/exit/q             - Exit client", "white")
            print_colored("\nExamples:", "cyan")
            print_colored("   call fetch url=https://google.com", "gray")
            print_colored("   transport webhook", "gray")
            print_colored("   call analyze url=https://example.com", "gray")
        
        else:
            print_colored(f"❌ Unknown command: {cmd}. Type 'help' for available commands.", "red")
        
        return True
    
    async def message_handler_http(self):
        """Handle HTTP transport messages."""
        if not self.http_read:
            return
        
        try:
            async for message in self.http_read:
                if isinstance(message, Exception):
                    print_colored(f"❌ [HTTP] Transport error: {message}", "red")
                else:
                    await self.handle_response(message.message.root)
        except Exception as e:
            print_colored(f"❌ [HTTP] Message handler error: {e}", "red")
    
    async def message_handler_webhook(self):
        """Handle webhook transport messages."""
        if not self.webhook_read:
            return
        
        try:
            async for message in self.webhook_read:
                if isinstance(message, Exception):
                    print_colored(f"❌ [Webhook] Transport error: {message}", "red")
                else:
                    await self.handle_response(message.message.root)
        except Exception as e:
            print_colored(f"❌ [Webhook] Message handler error: {e}", "red")
    
    async def interactive_cli(self):
        """Interactive CLI loop."""
        print_colored("🔗 Multi-Transport MCP Client", "blue")
        print_colored("Commands: init, tools, call <tool> <params>, transport [http|webhook], help, quit", "blue")
        print_colored(f"Current transport: {self.current_transport.upper()}", "blue")
        
        while True:
            try:
                command = input(f"\n[{self.current_transport.upper()}] > ").strip()
                
                if not command:
                    continue
                
                result = await self.process_command(command)
                if not result:
                    break
                    
            except KeyboardInterrupt:
                print_colored("\n👋 Goodbye!", "yellow")
                break
            except EOFError:
                print_colored("\n👋 Goodbye!", "yellow")
                break


@click.command()
@click.option(
    "--http-port",
    type=int,
    default=8000,
    help="Port of the StreamableHTTP server"
)
@click.option(
    "--webhook-port",
    type=int,
    default=8001,
    help="Port of the webhook server"
)
@click.option(
    "--client-port",
    type=int,
    default=8002,
    help="Port for webhook client responses"
)
def main(http_port, webhook_port, client_port) -> int:
    print_colored("🚀 Multi-Transport MCP Client", "cyan")
    print_colored(f"   HTTP Server: http://localhost:{http_port}/mcp", "blue")
    print_colored(f"   Webhook Server: http://localhost:{webhook_port}/mcp/request", "yellow")
    print_colored(f"   Client Webhook: http://localhost:{client_port}/webhook/response", "yellow")
    
    async def arun():
        # Create client
        client = MultiTransportClient(
            http_server_url=f"http://localhost:{http_port}/mcp",
            webhook_server_url=f"http://localhost:{webhook_port}/mcp/request",
            webhook_client_port=client_port
        )
        
        try:
            print_colored("🔧 Connecting to both transports...", "cyan")
            
            # Connect to both transports
            async with streamablehttp_client(client.http_server_url) as (http_read, http_write, _):
                client.http_read = http_read
                client.http_write = http_write
                
                async with webhook_client(client.webhook_config, client.webhook_url) as (webhook_read, webhook_write):
                    client.webhook_read = webhook_read
                    client.webhook_write = webhook_write
                    
                    print_colored("✅ Connected to both HTTP and webhook transports", "green")
                    
                    # Start message handlers and interactive CLI
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(client.message_handler_http)
                        tg.start_soon(client.message_handler_webhook)
                        tg.start_soon(client.interactive_cli)
        
        except KeyboardInterrupt:
            print_colored("\n👋 Client stopped", "yellow")
        except Exception as e:
            print_colored(f"❌ Client error: {e}", "red")
            return 1
        
        return 0
    
    try:
        result = anyio.run(arun)
        return result if result is not None else 0
    except Exception as e:
        print_colored(f"❌ Fatal error: {e}", "red")
        return 1


if __name__ == "__main__":
    main()