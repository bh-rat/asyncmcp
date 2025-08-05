#!/usr/bin/env python3
"""
Example MCP Client using StreamableHTTP + Webhook transport.

This client demonstrates:
- HTTP requests to MCP server
- SSE streaming for standard tool responses
- Webhook handler for tools marked with @webhook_tool
- Single session management for both transport methods
"""

import asyncio
import json
import logging
import sys
import time
import traceback

import anyio
import click
import uvicorn
from shared import DEFAULT_INIT_PARAMS, print_colored, send_mcp_notification, send_mcp_request
from starlette.applications import Starlette
from starlette.routing import Route

from asyncmcp.streamable_http_webhook import (
    StreamableHTTPWebhookClientConfig,
    streamable_http_webhook_client,
)


class InteractiveStreamableHTTPWebhookClient:
    """Interactive CLI client for StreamableHTTP + Webhook transport."""

    def __init__(self, config: StreamableHTTPWebhookClientConfig):
        self.config = config
        self.read_stream = None
        self.write_stream = None
        self.available_tools = {}
        self.webhook_tools = set()
        self.initialized = False
        self.pending_responses = {}  # Store responses by request_id

        # Set up logging
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def handle_webhook_response(self, response_data):
        """Handle webhook response and display it."""
        try:
            print()  # New line to interrupt CLI prompt
            print_colored("📥 [Webhook Response Received]", "cyan")

            # Parse the response if it's JSON-RPC format
            if hasattr(response_data, "content"):
                for content in response_data.content:
                    if hasattr(content, "text"):
                        print_colored("📄 Response:", "green")
                        print(content.text[:1000] + "..." if len(content.text) > 1000 else content.text)
            else:
                print_colored("📄 Raw Response:", "green")
                print(str(response_data))

            print("\n[StreamableHTTP+Webhook] > ", end="")
            sys.stdout.flush()

        except Exception as e:
            print_colored(f"❌ Error handling webhook response: {e}", "red")

    async def handle_sse_response(self, response):
        """Handle SSE response and display it."""
        try:
            if hasattr(response, "content"):
                for content in response.content:
                    if hasattr(content, "text"):
                        print_colored("📄 [SSE] Response:", "green")
                        print(content.text[:1000] + "..." if len(content.text) > 1000 else content.text)
            else:
                print_colored("📄 [SSE] Raw Response:", "green")
                print(str(response))
        except Exception as e:
            print_colored(f"❌ Error handling SSE response: {e}", "red")

    async def message_handler(self):
        """Background task to handle incoming messages from read_stream."""
        if not self.read_stream:
            self.logger.warning("No read_stream available for message handler")
            return

        self.logger.info("Starting message handler")
        try:
            async for message in self.read_stream:
                if isinstance(message, Exception):
                    self.logger.error(f"Transport error: {message}")
                    print_colored(f"❌ Transport error: {message}", "red")
                    continue

                # Handle the message
                await self.handle_message(message)
        except Exception as e:
            self.logger.error(f"Message handler error: {e}")
            print_colored(f"❌ Message handler error: {e}", "red")

    async def handle_message(self, session_message):
        """Handle incoming MCP messages."""
        try:
            message = session_message.message.root
            self.logger.debug(f"Processing message: {type(message).__name__} with id: {getattr(message, 'id', 'N/A')}")

            # Check if this is a response to a request we're waiting for
            if hasattr(message, "id"):
                self.logger.debug(
                    f"Message has ID: {message.id}, pending requests: {list(self.pending_responses.keys())}"
                )
                if message.id in self.pending_responses:
                    self.logger.debug(f"Found matching pending request for ID: {message.id}")
                    # Store the response for the waiting request
                    self.pending_responses[message.id] = session_message
                    return
                else:
                    self.logger.debug(f"Storing response for request ID: {message.id}")
                    self.pending_responses[message.id] = session_message
                    return

            # Handle other message types (notifications, etc.)
            if hasattr(message, "error"):
                self.logger.error(f"Server error: {message.error}")
                print_colored(f"❌ Server error: {message.error}", "red")
            elif hasattr(message, "result"):
                self.logger.info(f"Unexpected result received: {message.result}")
                print_colored("📥 Unexpected result received:", "blue")
                print(message.result)
            else:
                self.logger.info(f"Unexpected message received: {message}")
                print_colored("📥 Unexpected message received:", "blue")
                print(message)

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            print_colored(f"❌ Error handling message: {e}", "red")

    async def wait_for_response_by_id(self, request_id, timeout=10.0):
        """Wait for a specific response by request ID."""
        start_time = time.time()
        self.logger.debug(f"Waiting for response with ID: {request_id}")

        while time.time() - start_time < timeout:
            if request_id in self.pending_responses:
                response = self.pending_responses.pop(request_id)
                self.logger.debug(f"Found response for ID: {request_id}")
                return response
            await anyio.sleep(0.1)

        self.logger.warning(f"Timeout waiting for response with ID: {request_id}")
        return None

    async def process_command(self, command: str) -> bool:
        """Process interactive command. Returns False to quit."""
        if not command.strip():
            return True

        parts = command.strip().split()
        cmd = parts[0].lower()

        if cmd in ["quit", "exit", "q"]:
            print_colored("👋 Goodbye!", "yellow")
            return False

        elif cmd == "help":
            print_colored("📖 Available commands:", "cyan")
            print_colored("   init                    - Initialize the client session", "white")
            print_colored("   tools                   - List available tools", "white")
            print_colored("   call <tool> <params>    - Call a tool", "white")
            print_colored("   status                  - Show connection status", "white")
            print_colored("   help                    - Show this help", "white")
            print_colored("   quit/exit/q             - Exit client", "white")
            print_colored("\nExamples:", "cyan")
            print_colored("   init                                    # Initialize session", "gray")
            print_colored("   tools                                   # List tools", "gray")
            print_colored("   call fetch_sync url=https://google.com      # SSE delivery", "gray")
            print_colored("   call analyze_async url=https://example.com  # Webhook delivery", "gray")
            print_colored("\n🔄 Tools are automatically routed (SSE vs Webhook)!", "green")

        elif cmd == "init":
            if not self.write_stream:
                print_colored("❌ No write stream available. Connection may have failed.", "red")
                return True

            print_colored("🔄 Initializing client session...", "cyan")
            try:
                # Send proper MCP initialize request
                request_id = int(time.time() * 1000) % 100000
                self.logger.debug(f"Sending initialize request with ID: {request_id}")

                init_params = DEFAULT_INIT_PARAMS.copy()
                init_params["_meta"] = {"webhookUrl": self.config.webhook_url}

                await send_mcp_request(self.write_stream, "initialize", init_params, request_id)
                self.logger.debug(f"Initialize request sent, waiting for response...")

                # Wait for initialize response
                response = await self.wait_for_response_by_id(request_id, timeout=10.0)
                if response:
                    message = response.message.root
                    if hasattr(message, "result") and "serverInfo" in message.result:
                        server_info = message.result["serverInfo"]

                        # Send the required notifications/initialized notification
                        # This completes the MCP handshake
                        print_colored("📤 Sending initialized notification...", "blue")
                        await send_mcp_notification(self.write_stream, "notifications/initialized", {})

                        self.initialized = True
                        print_colored("✅ Client session initialized", "green")
                        print_colored(f"📋 Server: {server_info.get('name', 'Unknown')}", "blue")
                        print_colored(f"📋 Version: {server_info.get('version', 'Unknown')}", "blue")
                        print_colored("💡 Ready to call tools! Use 'tools' to list available tools.", "cyan")
                    else:
                        print_colored("❌ Invalid initialize response", "red")
                else:
                    print_colored("❌ No response received from server", "red")

            except Exception as e:
                print_colored(f"❌ Initialization failed: {e}", "red")
                print_colored(f"Traceback: {traceback.format_exc()}", "red")

        elif cmd == "tools":
            if not self.initialized:
                print_colored("❌ Please run 'init' first to initialize the session", "red")
                return True

            if not self.write_stream:
                print_colored("❌ No write stream available", "red")
                return True

            try:
                print_colored("📋 Listing available tools...", "blue")

                request_id = int(time.time() * 1000) % 100000

                await send_mcp_request(self.write_stream, "tools/list", {}, request_id)

                # Wait for tools response
                response = await self.wait_for_response_by_id(request_id, timeout=10.0)
                if response:
                    message = response.message.root
                    if hasattr(message, "result") and "tools" in message.result:
                        tools = message.result["tools"]
                        self.available_tools = {tool["name"]: tool for tool in tools}

                        print_colored(f"✅ Found {len(tools)} tools:", "green")
                        for tool in tools:
                            # Determine delivery method based on tool name patterns
                            delivery = "🌐 Webhook" if "async" in tool["name"] else "📡 SSE"
                            print_colored(
                                f"   • {tool['name']} ({delivery}): {tool.get('description', 'No description')}", "gray"
                            )
                    else:
                        print_colored("❌ Invalid tools response", "red")
                else:
                    print_colored("❌ No response received from server", "red")

            except Exception as e:
                print_colored(f"❌ Failed to list tools: {e}", "red")
                print_colored(f"Traceback: {traceback.format_exc()}", "red")

        elif cmd == "call":
            if not self.initialized:
                print_colored("❌ Please run 'init' first to initialize the session", "red")
                return True

            if len(parts) < 2:
                print_colored("❌ Usage: call <tool_name> <params...>", "red")
                return True

            tool_name = parts[1]
            param_parts = parts[2:]
            arguments = {}

            # Parse parameters (key=value format)
            for param in param_parts:
                if "=" in param:
                    key, value = param.split("=", 1)
                    arguments[key] = value
                else:
                    # Treat as positional parameter
                    param_index = len([k for k in arguments.keys() if k.startswith("param")])
                    arguments[f"param{param_index}"] = param

            if not self.write_stream:
                print_colored("❌ No write stream available", "red")
                return True

            try:
                delivery_type = "🌐 Webhook" if "async" in tool_name else "📡 SSE"
                print_colored(f"🔄 Calling {tool_name} ({delivery_type})...", "cyan")

                # Send proper MCP tools/call request
                request_id = int(time.time() * 1000) % 100000

                call_params = {"name": tool_name, "arguments": arguments}

                await send_mcp_request(self.write_stream, "tools/call", call_params, request_id)

                # For SSE tools, wait for immediate response
                if "async" not in tool_name:
                    print_colored("📡 Waiting for SSE response...", "blue")
                    response = await self.wait_for_response_by_id(request_id, timeout=30.0)
                    if response:
                        message = response.message.root
                        if hasattr(message, "result"):
                            await self.handle_sse_response(message.result)
                        elif hasattr(message, "error"):
                            print_colored(f"❌ Tool error: {message.error}", "red")
                        else:
                            print_colored("❌ Invalid tool response", "red")
                    else:
                        print_colored("❌ No response received from server", "red")
                else:
                    print_colored("⏳ Webhook response will arrive via webhook...", "yellow")

            except Exception as e:
                print_colored(f"❌ Tool call failed: {e}", "red")
                print_colored(f"Traceback: {traceback.format_exc()}", "red")

        elif cmd == "status":
            print_colored("📊 Connection Status:", "cyan")
            print_colored(f"   Server URL: {self.config.server_url}", "blue")
            print_colored(f"   Webhook URL: {self.config.webhook_url}", "blue")
            print_colored(f"   Client ID: {self.config.client_id}", "blue")
            print_colored(
                f"   Initialized: {'✅ Yes' if self.initialized else '❌ No'}", "green" if self.initialized else "red"
            )
            print_colored(f"   Available tools: {len(self.available_tools)}", "blue")

        else:
            print_colored(f"❌ Unknown command: {cmd}. Type 'help' for available commands.", "red")

        return True

    async def interactive_cli(self):
        """Interactive CLI loop."""
        print_colored("🔗 StreamableHTTP + Webhook MCP Client", "blue")
        print_colored("🚀 Interactive client with SSE and Webhook support", "green")
        print_colored("Commands: init, tools, call <tool> <params>, status, help, quit", "blue")
        print_colored("💡 Tools are automatically routed to SSE or Webhook delivery!", "cyan")

        while True:
            try:
                # Use async input to avoid blocking the event loop
                command = await anyio.to_thread.run_sync(lambda: input(f"\n[StreamableHTTP+Webhook] > "))
                command = command.strip()

                if not command:
                    continue

                result = await self.process_command(command)
                if not result:
                    break

            except KeyboardInterrupt:
                print_colored(f"\n👋 Goodbye!", "yellow")
                break
            except Exception as e:
                print_colored(f"❌ CLI error: {e}", "red")


async def run_client_session(config: StreamableHTTPWebhookClientConfig):
    """Run the interactive client session with StreamableHTTP + Webhook transport."""
    interactive_client = InteractiveStreamableHTTPWebhookClient(config)

    async with streamable_http_webhook_client(config) as (read_stream, write_stream, client):
        print_colored("🔗 Connected to StreamableHTTP + Webhook transport", "green")

        # Get webhook callback for web app integration
        webhook_callback = await client.get_webhook_callback()

        # Create a simple web app to handle webhooks with interactive response handling
        async def webhook_handler(request):
            response = None
            try:
                response = await webhook_callback(request)

                # Try to extract and display the webhook response data
                if response.status_code == 200:
                    # Parse the response body to extract tool results
                    body = await request.body()
                    if body:
                        try:
                            response_data = json.loads(body)
                            await interactive_client.handle_webhook_response(response_data)
                        except Exception:
                            print_colored("📥 [Webhook] Response received (raw data)", "cyan")
                else:
                    print_colored(f"❌ Webhook processing failed: {response.status_code}", "red")

                return response
            except Exception as e:
                print_colored(f"❌ Webhook handler error: {e}", "red")
                # Return a default response if webhook_callback failed
                if response is None:
                    from starlette.responses import Response

                    return Response(status_code=500, content="Webhook handler error")
                return response

        routes = [
            Route("/webhook", webhook_handler, methods=["POST"]),
        ]
        webhook_app = Starlette(routes=routes)

        # Start webhook server in background
        webhook_config = uvicorn.Config(
            app=webhook_app,
            host="localhost",
            port=8001,
            log_level="warning",
        )
        webhook_server = uvicorn.Server(webhook_config)

        async def run_webhook_server():
            await webhook_server.serve()

        # Set streams in interactive client (no need for ClientSession)
        interactive_client.read_stream = read_stream
        interactive_client.write_stream = write_stream

        print_colored("🔗 Client session established", "green")
        print_colored("💡 Run 'init' to initialize, then 'help' for commands", "blue")

        # Run webhook server, message handler, and interactive CLI concurrently
        async with anyio.create_task_group() as tg:
            print_colored("🔧 Starting background tasks...", "blue")
            tg.start_soon(run_webhook_server)

            print_colored("🔧 Starting message handler...", "blue")
            tg.start_soon(interactive_client.message_handler)  # Start background message handler

            # Give webhook server time to start
            await asyncio.sleep(0.5)
            print_colored(f"🌐 Webhook server listening on http://localhost:8001/webhook", "blue")

            # Start interactive CLI
            await interactive_client.interactive_cli()


@click.command()
@click.option(
    "--server-url",
    default="http://localhost:8000/mcp",
    help="MCP server URL",
)
@click.option(
    "--webhook-url",
    default="http://localhost:8001/webhook",
    help="Client webhook URL",
)
@click.option(
    "--client-id",
    default="streamable-http-webhook-client",
    help="Client identifier",
)
def main(server_url, webhook_url, client_id) -> int:
    # Set up debug logging
    logging.basicConfig(
        level=logging.DEBUG, format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    print_colored("🚀 Starting MCP Client with StreamableHTTP + Webhook Transport", "cyan")
    print_colored(f"   Server: {server_url}", "blue")
    print_colored(f"   Webhook: {webhook_url}", "blue")
    print_colored(f"   Client ID: {client_id}", "blue")

    # Configure client
    config = StreamableHTTPWebhookClientConfig(
        server_url=server_url,
        webhook_url=webhook_url,
        client_id=client_id,
        timeout_seconds=30.0,
        max_retries=1,
    )

    try:
        anyio.run(run_client_session, config)
        return 0
    except KeyboardInterrupt:
        print_colored("\n👋 Client stopped", "yellow")
        return 0
    except Exception as e:
        print_colored(f"❌ Client error: {e}", "red")
        print_colored(f"Traceback: {traceback.format_exc()}", "red")
        return 1


if __name__ == "__main__":
    main()
