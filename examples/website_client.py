#!/usr/bin/env python3
"""
Sample MCP CLI Client which accepts a tool call and sends it to the server
"""

import sys
import time

import anyio
import click
import mcp.types as types
from mcp.shared.message import SessionMessage
from shared import (
    DEFAULT_INIT_PARAMS,
    TRANSPORT_SNS_SQS,
    TRANSPORT_SQS,
    create_client_transport_config,
    print_colored,
    print_json,
    send_mcp_request,
)

from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sqs.client import sqs_client as pure_sqs_client


async def send_request(write_stream, method: str, params: dict = None):
    request_id = int(time.time() * 1000) % 100000
    await send_mcp_request(write_stream, method, params, request_id)


async def handle_message(session_message: SessionMessage):
    message = session_message.message.root
    await handle_response(message)


async def handle_response(message):
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


async def interactive_loop(write_stream):
    while True:
        try:
            if sys.stdin.isatty():
                print_colored("> ", "cyan")
                line = await anyio.to_thread.run_sync(lambda: sys.stdin.readline())

                if not line:  # EOF
                    break

                command = line.strip()
                if not command:
                    continue

                should_continue = await process_command(command, write_stream)
                if not should_continue:
                    break

        except KeyboardInterrupt:
            print_colored("\n👋 Goodbye!", "yellow")
            break
        except EOFError:
            print_colored("\n👋 Goodbye!", "yellow")
            break


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
        await send_request(write_stream, "initialize", DEFAULT_INIT_PARAMS)
        await anyio.sleep(1)  # Brief pause to ensure response is processed
        await send_initialized_notification(write_stream)
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


async def listen_for_messages(read_stream):
    try:
        while True:
            try:
                session_message = await read_stream.receive()
                if isinstance(session_message, Exception):
                    print_colored(f"❌ Message error: {session_message}", "red")
                    continue
                await handle_message(session_message)
            except anyio.get_cancelled_exc_class():
                break
            except Exception as e:
                print_colored(f"❌ Listener error: {str(e)}", "red")
                # Continue listening despite errors
                continue
    except anyio.get_cancelled_exc_class():
        print_colored("👂 Message listener stopped", "yellow")


@click.command()
@click.option(
    "--transport",
    type=click.Choice([TRANSPORT_SNS_SQS, TRANSPORT_SQS], case_sensitive=False),
    default=TRANSPORT_SNS_SQS,
    help="Transport layer to use",
)
def main(transport):
    anyio.run(lambda: interactive_mode(transport))


async def interactive_mode(transport_type: str = TRANSPORT_SNS_SQS):
    print_colored("Commands: init, tools, call <tool_name> <params...>, quit", "yellow")
    print_colored("Example: call fetch url=https://google.com", "yellow")

    if transport_type == TRANSPORT_SNS_SQS:
        transport_config, sqs_client, sns_client = create_client_transport_config(
            client_id="website-client", transport_type=transport_type
        )
        client_topic_arn = "arn:aws:sns:us-east-1:000000000000:mcp-response"
    else:
        transport_config, sqs_client, sns_client = create_client_transport_config(transport_type=transport_type)
        client_topic_arn = None

    try:
        if transport_type == TRANSPORT_SNS_SQS:
            client = sns_sqs_client
            client_args = (transport_config, sqs_client, sns_client, client_topic_arn)
        else:
            client = pure_sqs_client
            client_args = (transport_config, sqs_client)

        async with client(*client_args) as (read_stream, write_stream):
            # Starts both message listener and command input concurrently
            async with anyio.create_task_group() as tg:
                tg.start_soon(listen_for_messages, read_stream)
                tg.start_soon(interactive_loop, write_stream)
    except (KeyboardInterrupt, Exception) as e:
        if isinstance(e, KeyboardInterrupt):
            print_colored("\n👋 Goodbye!", "yellow")
        else:
            print_colored(f"❌ Transport error: {e}", "red")


if __name__ == "__main__":
    main()
