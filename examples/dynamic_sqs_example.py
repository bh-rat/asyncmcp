#!/usr/bin/env python3
"""
In this example:
- Server listens on one request queue for all clients
- Each client specifies their response queue in the initialize request
- Server creates sessions dynamically and routes responses to client-specific queues.
- Server can manage multiple sessions.
"""

import asyncio
import sys
import time

import anyio
import boto3
import mcp.types as types
from mcp.server.lowlevel.server import Server
from mcp.shared.message import SessionMessage

from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.manager import SqsSessionManager
from asyncmcp.sqs.utils import SqsClientConfig, SqsServerConfig

# Setup LocalStack AWS clients
AWS_CONFIG = {
    "region_name": "us-east-1",
    "endpoint_url": "http://localhost:4566",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}

# Queue URLs
SERVER_REQUEST_QUEUE = "http://localhost:4566/000000000000/mcp-processor"
CLIENT_RESPONSE_QUEUE = "http://localhost:4566/000000000000/mcp-consumer"


async def ensure_queues_exist():
    """Create SQS queues if they don't exist."""
    sqs_client = boto3.client("sqs", **AWS_CONFIG)

    queues_to_create = ["mcp-processor", "mcp-consumer"]

    for queue_name in queues_to_create:
        try:
            sqs_client.create_queue(QueueName=queue_name)
        except Exception as e:
            if "QueueAlreadyExists" in str(e) or "already exists" in str(e):
                pass  # Queue already exists, which is fine
            else:
                print(f"❌ Error with queue {queue_name}: {e}")
                raise


async def run_server():
    """Run the SQS server with session manager."""
    # Ensure queues exist
    await ensure_queues_exist()

    # Create MCP server
    app = Server("dynamic-sqs-example")

    @app.call_tool()
    async def ping_tool(name: str, arguments: dict):
        if name == "ping":
            return [types.TextContent(type="text", text="pong")]

    @app.list_tools()
    async def list_tools():
        tools = [
            types.Tool(name="ping", description="Simple ping tool", inputSchema={"type": "object", "properties": {}})
        ]
        return tools

    # Configure SQS transport (no write_queue_url needed)
    config = SqsServerConfig(
        read_queue_url=SERVER_REQUEST_QUEUE,
        max_messages=10,
        wait_time_seconds=5,
        poll_interval_seconds=2.0,
    )

    sqs_client = boto3.client("sqs", **AWS_CONFIG)

    # Create session manager
    session_manager = SqsSessionManager(app=app, config=config, sqs_client=sqs_client)

    print("🚀 Starting SQS server with dynamic queue support...")
    print(f"📡 Listening on: {SERVER_REQUEST_QUEUE}")
    print("📝 Send initialize request with 'response_queue_url' parameter to start a session")

    async with session_manager.run():
        try:
            while True:
                await asyncio.sleep(10)
                stats = session_manager.get_all_sessions()
                if stats:
                    print(f"🔗 Active sessions: {len(stats)}")
                    for session_id, stat in stats.items():
                        terminated = stat.get("terminated", False)
                        status = "terminated" if terminated else "active"
                        print(f"  Session {session_id[:8]}...: {status}")
        except KeyboardInterrupt:
            print("🛑 Server shutting down...")


async def run_client():
    """Run a simple SQS client that specifies its response queue."""
    # Ensure queues exist
    await ensure_queues_exist()

    # Purge the response queue to clear old messages
    purge_client = boto3.client("sqs", **AWS_CONFIG)
    try:
        purge_client.purge_queue(QueueUrl=CLIENT_RESPONSE_QUEUE)

        # Additional manual cleanup - receive and delete any remaining messages
        cleanup_count = 0
        while True:
            response = purge_client.receive_message(
                QueueUrl=CLIENT_RESPONSE_QUEUE, MaxNumberOfMessages=10, WaitTimeSeconds=1
            )
            messages = response.get("Messages", [])
            if not messages:
                break

            for message in messages:
                purge_client.delete_message(QueueUrl=CLIENT_RESPONSE_QUEUE, ReceiptHandle=message["ReceiptHandle"])
                cleanup_count += 1

        if cleanup_count > 0:
            print(f"✅ Cleaned {cleanup_count} old messages from queue")

        # Small delay to ensure cleanup is processed
        time.sleep(1)

    except Exception as e:
        print(f"⚠️ Warning: Could not clean queue: {e}")

    # Configure client (reads from server's request queue, responds to our queue)
    config = SqsClientConfig(
        read_queue_url=SERVER_REQUEST_QUEUE,  # Server's request queue
        response_queue_url=CLIENT_RESPONSE_QUEUE,
        client_id="dynamic-example-client",
    )

    sqs_client_instance = boto3.client("sqs", **AWS_CONFIG)

    print("🔧 Starting SQS client...")
    print(f"📤 Sending requests to: {SERVER_REQUEST_QUEUE}")
    print(f"📥 Listening for responses on: {CLIENT_RESPONSE_QUEUE}")

    async with sqs_client(config, sqs_client_instance) as (read_stream, write_stream):
        # Send initialize request with our response queue URL
        init_request = types.JSONRPCMessage(
            root=types.JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
                    "clientInfo": {"name": "dynamic-example-client", "version": "1.0.0"},
                    "response_queue_url": CLIENT_RESPONSE_QUEUE,  # Tell server where to send responses
                },
            )
        )

        print("📤 Sending initialize request...")
        await write_stream.send(SessionMessage(init_request))

        # Wait for initialize response with timeout
        print("⏳ Waiting for initialize response...")
        try:
            with anyio.move_on_after(30):  # 30 second timeout
                response = await read_stream.receive()
                if isinstance(response, Exception):
                    print(f"❌ Error: {response}")
                    return

                print("✅ Initialized!")
        except Exception as e:
            print(f"❌ Timeout or error waiting for initialize response: {e}")
            return

        # Send "initialized" notification (required by MCP spec)
        initialized_notification = types.JSONRPCMessage(
            root=types.JSONRPCNotification(
                jsonrpc="2.0",
                method="notifications/initialized",
                params={},
            )
        )

        print("📤 Sending initialized notification...")
        await write_stream.send(SessionMessage(initialized_notification))

        # Longer delay to ensure initialized notification is fully processed by MCP server
        await anyio.sleep(2.0)  # Increased from 0.1 to 2.0 seconds

        # Send tools/list request
        tools_request = types.JSONRPCMessage(
            root=types.JSONRPCRequest(
                jsonrpc="2.0",
                id=2,
                method="tools/list",
                params={},
            )
        )

        print("📤 Requesting tools list...")
        await write_stream.send(SessionMessage(tools_request))

        # Wait for tools response
        response = await read_stream.receive()
        if isinstance(response, Exception):
            print(f"❌ Error: {response}")
            return

        print("✅ Got tools list!")

        # Parse the tools response properly
        try:
            message_root = response.message.root

            if hasattr(message_root, "result") and message_root.result:
                tools = message_root.result.get("tools", [])
                if tools:
                    print(f"🔧 Available tools ({len(tools)}):")
                    for tool in tools:
                        name = tool.get("name", "Unknown")
                        description = tool.get("description", "No description")
                        print(f"  - {name}: {description}")

                    # Now call the ping tool to demonstrate tools/call
                    print("\n📤 Calling ping tool...")

                    ping_call_request = types.JSONRPCMessage(
                        root=types.JSONRPCRequest(
                            jsonrpc="2.0", id=3, method="tools/call", params={"name": "ping", "arguments": {}}
                        )
                    )

                    await write_stream.send(SessionMessage(ping_call_request))

                    # Wait for ping response
                    ping_response = await read_stream.receive()
                    if isinstance(ping_response, Exception):
                        print(f"❌ Error calling ping: {ping_response}")
                    else:
                        print("✅ Got ping response!")

                        try:
                            ping_result = ping_response.message.root
                            if hasattr(ping_result, "result"):
                                result = ping_result.result
                                if isinstance(result, dict) and "content" in result:
                                    content = result["content"]
                                    if isinstance(content, list) and len(content) > 0:
                                        first_content = content[0]
                                        if hasattr(first_content, "text"):
                                            print(f"🏓 Ping result: {first_content.text}")
                                        elif isinstance(first_content, dict) and "text" in first_content:
                                            print(f"🏓 Ping result: {first_content['text']}")
                                        else:
                                            print(f"🏓 Ping result: {first_content}")
                                    else:
                                        print(f"🏓 Ping result: {content}")
                                else:
                                    print(f"🏓 Ping result: {result}")
                            else:
                                print(f"🏓 Ping response: {ping_result}")
                        except Exception as e:
                            print(f"❌ Error parsing ping response: {e}")

                else:
                    print("🔧 No tools available")
            else:
                print(f"🔧 Unexpected response format")
        except Exception as e:
            print(f"❌ Error parsing tools response: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "client":
        print("🚀 Running client example...")
        asyncio.run(run_client())
    else:
        print("🚀 Running server example...")
        asyncio.run(run_server())

    print("\nTo run the client in another terminal:")
    print("uv run examples/dynamic_sqs_example.py client")
