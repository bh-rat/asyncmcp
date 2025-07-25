#!/usr/bin/env python3
"""
Sample MCP CLI Client using webhook transport
"""

import sys
import time

import anyio
import click
import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp.webhook.client import webhook_client
from shared import (
    print_colored,
    print_json,
    create_client_transport_config,
    send_mcp_request,
    DEFAULT_INIT_PARAMS,
    TRANSPORT_WEBHOOK,
)

# Add a global flag to track initialization
_init_complete = False


async def send_request(write_stream, method: str, params: dict = None):
    request_id = int(time.time() * 1000) % 100000
    await send_mcp_request(write_stream, method, params, request_id)


async def handle_message(session_message: SessionMessage):
    message = session_message.message.root
    await handle_response(message)


async def handle_response(message):
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
        _init_complete = True  # Mark initialization as complete
        return

    if "tools" in result:
        tools = result["tools"]
        print_colored(f"✅ Found {len(tools)} tools:", "green")
        for tool in tools:
            print_colored(f"   • {tool.get('name', 'Unknown')}: {tool.get('description', 'No description')}", "white")
        return

    if "content" in result:
        content = result["content"]
        print_colored("✅ Tool result:", "green")
        for item in content:
            if item.get("type") == "text":
                print_colored(f"   📄 {item.get('text', '')}", "white")
            else:
                print_json(item, "Content Item")
        return

    # Default case for other dict results
    print_colored("✅ Response received:", "green")
    print_json(result)


async def send_initialized_notification(write_stream):
    notification = types.JSONRPCMessage.model_validate(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )

    session_message = SessionMessage(notification)
    await write_stream.send(session_message)
    print_colored("📤 Sent initialized notification", "cyan")


async def process_command(command: str, write_stream):
    """Process a single command"""
    parts = command.split()

    if not parts:
        return

    cmd = parts[0].lower()

    if cmd in ["quit", "exit", "q"]:
        print_colored("👋 Goodbye!", "yellow")
        sys.exit(0)
    elif cmd == "init":
        global _init_complete
        _init_complete = False  # Reset flag

        await send_request(write_stream, "initialize", DEFAULT_INIT_PARAMS)

        # Wait for initialize response with timeout
        print_colored("⏳ Waiting for initialize response...", "yellow")
        try:
            with anyio.move_on_after(30):  # 30 second timeout
                # Wait for the actual initialize response
                while not _init_complete:
                    await anyio.sleep(0.1)
        except Exception as e:
            print_colored(f"❌ Timeout or error waiting for initialize response: {e}", "red")
            return

        await send_initialized_notification(write_stream)

        # Brief delay to ensure initialized notification is processed
        await anyio.sleep(0.5)
    elif cmd == "tools":
        await send_request(write_stream, "tools/list", {})
    elif cmd == "call":
        if len(parts) >= 2:
            tool_name = parts[1]
            param_parts = parts[2:]
            arguments = {}
            for param in param_parts:
                if "=" in param:
                    # Handle key=value format
                    key, value = param.split("=", 1)
                    arguments[key] = value
                else:
                    param_index = len([k for k in arguments.keys() if k.startswith("param")])
                    arguments[f"param{param_index}"] = param

            params = {"name": tool_name, "arguments": arguments}
            await send_request(write_stream, "tools/call", params)
        else:
            print_colored("❌ Usage: call <tool_name> <params...>", "red")

    else:
        print_colored(f"❌ Unknown command: {cmd}", "red")

    return True


async def message_handler(read_stream):
    """Handle incoming messages from the server"""
    async for message in read_stream:
        if isinstance(message, Exception):
            print_colored(f"❌ Transport error: {message}", "red")
        else:
            await handle_message(message)


async def interactive_cli(write_stream):
    """Interactive CLI loop"""
    print_colored("Quick Interactive MCP Client", "blue")
    print_colored("Commands: init, tools, call <tool_name> <params...>, quit", "blue")
    print_colored("Example: call fetch url=https://google.com", "blue")

    while True:
        try:
            # Read command from user
            command = input("🔗 Connected to MCP transport\n> ").strip()

            if not command:
                continue

            # Process the command
            result = await process_command(command, write_stream)
            if result is False:
                break

        except KeyboardInterrupt:
            print_colored("\n👋 Goodbye!", "yellow")
            break
        except EOFError:
            print_colored("\n👋 Goodbye!", "yellow")
            break


@click.command()
@click.option(
    "--server-port",
    type=int,
    default=8000,
    help="Server port to connect to",
)
@click.option(
    "--webhook-port",
    type=int,
    default=8001,
    help="Port for webhook server",
)
@click.option(
    "--client-id",
    type=str,
    default="webhook-client",
    help="Client ID for identification",
)
def main(server_port, webhook_port, client_id) -> int:
    print_colored("🚀 Starting MCP Webhook Client", "cyan")

    async def arun():
        # Configure webhook transport
        print_colored("🔧 Configuring webhook transport", "yellow")

        # Create custom webhook config
        from asyncmcp.webhook.utils import WebhookTransportConfig

        config = WebhookTransportConfig(
            server_url=f"http://localhost:{server_port}/mcp/request",
            webhook_url=f"http://localhost:{webhook_port}/webhook/response",
            client_id=client_id,
        )

        async with webhook_client(config) as (read_stream, write_stream):
            print_colored("📡 Client connected to webhook transport", "green")

            # Start message handler and interactive CLI concurrently
            async with anyio.create_task_group() as tg:
                tg.start_soon(message_handler, read_stream)
                tg.start_soon(interactive_cli, write_stream)

    anyio.run(arun)
    return 0


if __name__ == "__main__":
    main()
