#!/usr/bin/env python3
"""
Multi-Transport MCP Client supporting both StreamableHTTP and Webhook transports.

This client demonstrates:
- StreamableHTTP client for synchronous tool calls (immediate response)
- Webhook client for asynchronous tool calls (webhook callback response)
- Interactive CLI for testing both transports
- Complete multi-transport client architecture
"""

import time
from typing import Dict

import anyio
import click
import mcp.types as types
import uvicorn
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage
from shared import DEFAULT_INIT_PARAMS, print_colored, print_json, send_mcp_request
from starlette.applications import Starlette
from starlette.routing import Route

from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.utils import WebhookClientConfig

# Global flag to track initialization
_init_complete = False


class MultiTransportClient:
    """
    Interactive multi-transport client that can communicate with both HTTP and webhook transports.
    """

    def __init__(self, http_server_url: str, webhook_server_url: str, webhook_client_port: int):
        self.http_server_url = http_server_url
        self.webhook_server_url = webhook_server_url
        self.webhook_client_port = webhook_client_port

        # Transport configurations
        self.webhook_config = WebhookClientConfig(
            server_url=webhook_server_url, client_id="multi-transport-client", timeout_seconds=30.0
        )
        self.webhook_client_port = webhook_client_port
        self.webhook_url = f"http://localhost:{webhook_client_port}/webhook/response"

        # Available tools and tool routing
        self.available_tools: Dict[str, types.Tool] = {}
        self.tool_routing_map: Dict[str, str] = {}  # tool_name -> transport_type
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
            self.tool_routing_map.clear()

            for tool in tools:
                tool_name = tool.get("name", "Unknown")
                self.available_tools[tool_name] = tool

                # Extract transport information from tool metadata
                input_schema = tool.get("inputSchema", {})
                meta = input_schema.get("_meta", {})
                transport = meta.get("transport", "http")  # Default to http
                self.tool_routing_map[tool_name] = transport

                print_colored(
                    f"   • {tool_name}: {tool.get('description', 'No description')} [transport: {transport}]", "white"
                )
                # Tool metadata processed

            # Tool routing map configured
            return

        if "content" in result:
            content = result["content"]
            print_colored("✅ Tool result:", "green")
            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
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
            print_colored("ℹ️  Transport switching is now automatic based on tool requirements", "yellow")
            print_colored(f"📡 Available transports: HTTP, Webhook", "blue")
            print_colored(f"🔄 Tool calls are automatically routed to the correct transport", "cyan")

        elif cmd == "init":
            global _init_complete
            _init_complete = False

            print_colored("🔄 Initializing both HTTP and Webhook transports...", "cyan")

            # Initialize HTTP transport first
            if not self.http_write:
                print_colored("❌ HTTP transport not connected", "red")
                return True

            print_colored("📡 Initializing HTTP transport...", "yellow")
            await self.send_request(self.http_write, "initialize", DEFAULT_INIT_PARAMS)

            # Wait for HTTP initialize response
            try:
                with anyio.move_on_after(15):
                    while not _init_complete:
                        await anyio.sleep(0.1)
            except Exception as e:
                print_colored(f"❌ Timeout waiting for HTTP initialize response: {e}", "red")
                return True

            await self.send_initialized_notification(self.http_write)
            print_colored("✅ HTTP transport initialized", "green")

            # Reset for webhook initialization
            _init_complete = False

            # Initialize Webhook transport
            if not self.webhook_write:
                print_colored("❌ Webhook transport not connected", "red")
                return True

            print_colored("🌐 Initializing Webhook transport...", "yellow")
            webhook_init_params = DEFAULT_INIT_PARAMS.copy()
            webhook_init_params["_meta"] = {"webhookUrl": self.webhook_url}
            await self.send_request(self.webhook_write, "initialize", webhook_init_params)

            # Wait for webhook initialize response
            try:
                with anyio.move_on_after(15):
                    while not _init_complete:
                        await anyio.sleep(0.1)
            except Exception as e:
                print_colored(f"❌ Timeout waiting for Webhook initialize response: {e}", "red")
                return True

            await self.send_initialized_notification(self.webhook_write)
            print_colored("✅ Webhook transport initialized", "green")

            print_colored("🎉 Both transports initialized successfully!", "green")

        elif cmd == "tools":
            # Use HTTP transport to list tools (standard MCP operation)
            if not self.http_write:
                print_colored("❌ HTTP transport not connected", "red")
                return True

            await self.send_request(self.http_write, "tools/list", {})

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

                # Automatically determine transport based on tool routing
                required_transport = self.tool_routing_map.get(tool_name, "http")

                if required_transport == "http":
                    write_stream = self.http_write
                    transport_name = "HTTP"
                elif required_transport == "webhook":
                    write_stream = self.webhook_write
                    transport_name = "Webhook"
                else:
                    print_colored(f"❌ Unknown transport type: {required_transport}", "red")
                    return True

                if not write_stream:
                    print_colored(f"❌ {transport_name} transport not connected", "red")
                    return True

                print_colored(f"📡 Auto-routing {tool_name} to {transport_name} transport", "cyan")
                params = {"name": tool_name, "arguments": arguments}
                await self.send_request(write_stream, "tools/call", params)
            else:
                print_colored("❌ Usage: call <tool_name> <params...>", "red")

        elif cmd == "help":
            print_colored("📖 Available commands:", "cyan")
            print_colored("   init                    - Initialize both HTTP and Webhook transports", "white")
            print_colored("   tools                   - List available tools with transport routing", "white")
            print_colored("   call <tool> <params>    - Call a tool (auto-routed to correct transport)", "white")
            print_colored("   transport               - Show transport status", "white")
            print_colored("   help                    - Show this help", "white")
            print_colored("   quit/exit/q             - Exit client", "white")
            print_colored("\nExamples:", "cyan")
            print_colored("   init                               # Initialize both transports", "gray")
            print_colored("   tools                              # List tools with routing info", "gray")
            print_colored("   call fetch_sync url=https://google.com    # Auto-routes to HTTP", "gray")
            print_colored("   call analyze_async url=https://example.com # Auto-routes to Webhook", "gray")
            print_colored("\n🔄 Tool calls are automatically routed to the correct transport!", "green")

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
            print_colored("⚠️ [Webhook] No webhook read stream available", "yellow")
            return

        print_colored("🌐 [Webhook] Message handler started", "cyan")
        try:
            async for message in self.webhook_read:
                if isinstance(message, Exception):
                    print_colored(f"❌ [Webhook] Transport error: {message}", "red")
                else:
                    # Print newline to interrupt CLI prompt and show response immediately
                    print()
                    print_colored("🌐 [Webhook Response Received]", "cyan")
                    await self.handle_response(message.message.root)
                    print("\n[AUTO-ROUTE] > ", end="")
                    import sys

                    sys.stdout.flush()
        except Exception as e:
            print_colored(f"❌ [Webhook] Message handler error: {e}", "red")
        finally:
            print_colored("⚠️ [Webhook] Message handler ended", "yellow")

    async def interactive_cli(self):
        """Interactive CLI loop."""
        print_colored("🔗 Multi-Transport MCP Client", "blue")
        print_colored("🚀 Unified client with automatic transport routing", "green")
        print_colored("Commands: init, tools, call <tool> <params>, transport, help, quit", "blue")
        print_colored("💡 Tool calls are automatically routed to the correct transport!", "cyan")

        while True:
            try:
                # Use async input to avoid blocking the event loop
                command = await anyio.to_thread.run_sync(lambda: input(f"\n[AUTO-ROUTE] > "), cancellable=True)
                command = command.strip()

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
@click.option("--http-port", type=int, default=8000, help="Port of the StreamableHTTP server")
@click.option("--webhook-port", type=int, default=8001, help="Port of the webhook server")
@click.option("--client-port", type=int, default=8002, help="Port for webhook client responses")
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
            webhook_client_port=client_port,
        )

        try:
            print_colored("🔧 Connecting to both transports...", "cyan")

            # Connect to HTTP transport first
            try:
                print_colored("📡 Connecting to HTTP transport...", "cyan")
                async with streamablehttp_client(client.http_server_url) as (http_read, http_write, _):
                    client.http_read = http_read
                    client.http_write = http_write
                    print_colored("✅ Connected to HTTP transport", "green")

                    # Connect to webhook transport
                    try:
                        print_colored("🌐 Connecting to webhook transport...", "cyan")
                        webhook_path = "/webhook/response"
                        async with webhook_client(client.webhook_config, webhook_path) as (
                            webhook_read,
                            webhook_write,
                            webhook_client_instance,
                        ):
                            client.webhook_read = webhook_read
                            client.webhook_write = webhook_write
                            print_colored("✅ Connected to webhook transport", "green")

                            # Set up webhook response server
                            print_colored("🌐 Starting webhook response server...", "cyan")
                            webhook_callback = await webhook_client_instance.get_webhook_callback()

                            routes = [
                                Route(webhook_path, webhook_callback, methods=["POST"]),
                            ]
                            webhook_server_app = Starlette(routes=routes)

                            webhook_server_config = uvicorn.Config(
                                app=webhook_server_app, host="localhost", port=client_port, log_level="warning"
                            )
                            webhook_server = uvicorn.Server(webhook_server_config)

                            print_colored("✅ Connected to both HTTP and webhook transports", "green")

                            # Start all components concurrently
                            try:
                                async with anyio.create_task_group() as tg:
                                    tg.start_soon(webhook_server.serve)
                                    tg.start_soon(client.message_handler_http)
                                    tg.start_soon(client.message_handler_webhook)
                                    tg.start_soon(client.interactive_cli)

                                    await anyio.sleep(0.1)  # Wait for webhook server to start
                                    print_colored(
                                        f"🔗 Webhook response server listening on http://localhost:{client_port}{webhook_path}",
                                        "blue",
                                    )
                            except Exception as task_error:
                                print_colored(f"❌ Task group error: {task_error}", "red")
                                raise
                    except Exception as webhook_error:
                        print_colored(f"❌ Webhook connection error: {webhook_error}", "red")
                        raise
            except Exception as http_error:
                print_colored(f"❌ HTTP connection error: {http_error}", "red")
                raise

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
