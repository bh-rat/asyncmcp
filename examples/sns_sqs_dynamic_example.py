#!/usr/bin/env python3

"""
SNS/SQS Dynamic Session Manager Example

This example demonstrates:
1. Server running with SnsSqsSessionManager to handle multiple clients
2. Client providing its own topic ARN during initialization
3. Dynamic session creation and management
4. Multiple concurrent clients supported

The client provides a 'client_topic_arn' parameter in the initialize request,
and the server creates a dedicated session for each client.
"""

import asyncio
import logging
import sys

import boto3
import mcp.types as types
from mcp.server import Server
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest

from asyncmcp import SnsSqsClientConfig, SnsSqsServerConfig, SnsSqsSessionManager, sns_sqs_client

# Setup logging
logging.basicConfig(level=logging.INFO)

# AWS Configuration for LocalStack
AWS_CONFIG = {
    "endpoint_url": "http://localhost:4566",  # LocalStack endpoint
    "region_name": "us-east-1",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}

# Queue and Topic Configuration
SERVER_REQUEST_QUEUE = "http://localhost:4566/000000000000/server-requests"
SERVER_SNS_TOPIC = "arn:aws:sns:us-east-1:000000000000:server-topic"
CLIENT_RESPONSE_QUEUE = "http://localhost:4566/000000000000/client-responses"
CLIENT_TOPIC = "arn:aws:sns:us-east-1:000000000000:client-topic"


async def ensure_resources_exist():
    """Ensure SQS queues and SNS topics exist."""
    sqs_client = boto3.client("sqs", **AWS_CONFIG)
    sns_client = boto3.client("sns", **AWS_CONFIG)

    # Create queues
    for queue_name in ["server-requests", "client-responses"]:
        try:
            sqs_client.create_queue(QueueName=queue_name)
            print(f"✅ Queue {queue_name} ready")
        except Exception as e:
            if "Queue already exists" in str(e):
                print(f"📂 Queue {queue_name} already exists")
            else:
                print(f"⚠️  Error creating queue {queue_name}: {e}")

    # Create topics
    for topic_name in ["server-topic", "client-topic"]:
        try:
            sns_client.create_topic(Name=topic_name)
            print(f"✅ Topic {topic_name} ready")
        except Exception as e:
            if "AlreadyExists" in str(e):
                print(f"📂 Topic {topic_name} already exists")
            else:
                print(f"⚠️  Topic {topic_name}: {e}")


async def run_server():
    """Run the SNS/SQS server with session manager."""
    await ensure_resources_exist()

    # Create MCP server
    app = Server("dynamic-sns-sqs-example")

    @app.call_tool()
    async def ping_tool(name: str, arguments: dict):
        if name == "ping":
            return [types.TextContent(type="text", text="pong via SNS/SQS")]

    @app.list_tools()
    async def list_tools():
        tools = [
            types.Tool(
                name="ping",
                description="Simple ping tool via SNS/SQS",
                inputSchema={"type": "object", "properties": {}},
            )
        ]
        return tools

    # Configure SNS/SQS server - only needs the queue it listens on
    config = SnsSqsServerConfig(
        sqs_queue_url=SERVER_REQUEST_QUEUE,
        max_messages=10,
        wait_time_seconds=5,
        poll_interval_seconds=2.0,
    )

    sqs_client = boto3.client("sqs", **AWS_CONFIG)
    sns_client = boto3.client("sns", **AWS_CONFIG)

    # Create session manager
    session_manager = SnsSqsSessionManager(app=app, config=config, sqs_client=sqs_client, sns_client=sns_client)

    print("🚀 Starting SNS/SQS server with dynamic topic support...")
    print(f"📡 Listening on queue: {SERVER_REQUEST_QUEUE}")
    print("📝 Send initialize request with 'client_topic_arn' parameter to start a session")

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
    """Run a test client that provides its own topic ARN."""
    await ensure_resources_exist()

    # Configure client - specifies where to listen and where to send
    config = SnsSqsClientConfig(
        sqs_queue_url=CLIENT_RESPONSE_QUEUE,  # Client listens on its response queue
        sns_topic_arn=SERVER_SNS_TOPIC,  # Client sends to server topic
        max_messages=5,
        wait_time_seconds=1,
        poll_interval_seconds=1.0,
        client_id="test-client-001",
    )

    sqs_client = boto3.client("sqs", **AWS_CONFIG)
    sns_client = boto3.client("sns", **AWS_CONFIG)

    print("🔌 Starting SNS/SQS client...")
    print(f"📥 Listening on queue: {CLIENT_RESPONSE_QUEUE}")
    print(f"📤 Sending to topic: {SERVER_SNS_TOPIC}")
    print(f"🎯 Client topic: {CLIENT_TOPIC}")

    async with sns_sqs_client(config, sqs_client, sns_client, CLIENT_TOPIC) as (read_stream, write_stream):
        # Send initialize request with client topic ARN
        initialize_request = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "sns-sqs-test-client", "version": "1.0"},
                    # Client provides its own topic ARN for responses
                    "client_topic_arn": CLIENT_TOPIC,
                },
            )
        )

        print("📋 Sending initialize request...")
        await write_stream.send(SessionMessage(initialize_request))

        # Wait for initialize response
        print("⏳ Waiting for initialize response...")
        init_response = await read_stream.receive()
        if isinstance(init_response, SessionMessage):
            print("✅ Received initialize response!")
            print(f"   Protocol version: {init_response.message.root.result.get('protocolVersion')}")

        # Send ping tool call
        ping_request = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=2,
                method="tools/call",
                params={"name": "ping", "arguments": {}},
            )
        )

        print("🏓 Sending ping request...")
        await write_stream.send(SessionMessage(ping_request))

        # Wait for ping response
        ping_response = await read_stream.receive()
        if isinstance(ping_response, SessionMessage):
            print("✅ Received ping response!")
            content = ping_response.message.root.result.get("content", [])
            if content and len(content) > 0:
                print(f"   Response: {content[0].text}")

        print("🎉 Client session completed successfully!")


def print_usage():
    print("""
Usage:
  python sns_sqs_dynamic_example.py server  # Run the session manager server
  python sns_sqs_dynamic_example.py client  # Run a test client

The server supports multiple concurrent clients, each providing their own
SNS topic ARN for receiving responses.
    """)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print_usage()
        sys.exit(1)

    mode = sys.argv[1].lower()

    if mode == "server":
        asyncio.run(run_server())
    elif mode == "client":
        asyncio.run(run_client())
    else:
        print_usage()
        sys.exit(1)
