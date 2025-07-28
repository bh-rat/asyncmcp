#!/usr/bin/env python3
"""
Test script to verify MCP initialize flow with the streamable HTTP webhook transport.
"""

import asyncio
import json
import httpx
from contextlib import asynccontextmanager
from typing import Any, Dict

import mcp.types as types
from mcp.server.lowlevel.server import Server as MCPServer
from src.asyncmcp.streamable_http_webhook.manager import StreamableHTTPWebhookSessionManager
from src.asyncmcp.streamable_http_webhook.utils import StreamableHTTPWebhookConfig


async def test_initialize_flow():
    """Test the MCP initialize flow."""

    # Create a simple MCP server
    server = MCPServer("test-server")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="test_tool",
                description="A test tool",
                inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
            )
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=f"Tool {name} called with: {arguments}")]

    # Create configuration
    config = StreamableHTTPWebhookConfig(
        timeout_seconds=30.0,
        webhook_max_retries=3,
        json_response=False,  # Use SSE streaming
    )

    # Create session manager
    session_manager = StreamableHTTPWebhookSessionManager(
        app=server, config=config, server_path="/mcp", stateless=False
    )

    # Test the initialize flow
    async with session_manager.run():
        # Simulate an initialize request
        initialize_request = {
            "jsonrpc": "2.0",
            "id": "init-1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
                "_meta": {"webhookUrl": "http://localhost:9999/webhook", "webhookTools": ["test_tool"]},
            },
        }

        # Create a fake ASGI scope, receive, and send for testing
        request_body = json.dumps(initialize_request).encode("utf-8")

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [
                (b"content-type", b"application/json"),
                (b"accept", b"application/json, text/event-stream"),
                (b"content-length", str(len(request_body)).encode()),
            ],
        }

        # Capture the response
        response_data = []
        response_headers = {}
        response_status = None

        async def receive():
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(message):
            nonlocal response_status, response_headers, response_data
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
            elif message["type"] == "http.response.body":
                if "body" in message:
                    response_data.append(message["body"])

        try:
            # Handle the request
            await session_manager.handle_request(scope, receive, send)

            # Check the response
            print(f"Response Status: {response_status}")
            print(f"Response Headers: {response_headers}")

            if response_data:
                body = b"".join(response_data).decode("utf-8")
                print(f"Response Body: {body}")

                # Parse and validate JSON response
                try:
                    response_json = json.loads(body)
                    print(f"Parsed Response: {response_json}")

                    # Check if it's a proper MCP initialize response
                    if "result" in response_json:
                        print("✅ SUCCESS: Received proper MCP initialize response!")
                        print(f"Server Info: {response_json['result'].get('serverInfo', 'Not found')}")
                        print(f"Capabilities: {response_json['result'].get('capabilities', 'Not found')}")
                        return True
                    else:
                        print("❌ ERROR: Response doesn't contain 'result' field")
                        return False
                except json.JSONDecodeError as e:
                    print(f"❌ ERROR: Invalid JSON response: {e}")
                    return False
            else:
                print("❌ ERROR: No response body received")
                return False

        except Exception as e:
            print(f"❌ ERROR: Exception during request handling: {e}")
            import traceback

            traceback.print_exc()
            return False


if __name__ == "__main__":
    result = asyncio.run(test_initialize_flow())
    if result:
        print("\n🎉 Test PASSED: MCP initialize flow working correctly!")
    else:
        print("\n💥 Test FAILED: MCP initialize flow broken!")
    exit(0 if result else 1)
