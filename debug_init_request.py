#!/usr/bin/env python3
"""
Debug the initialize request to see exactly what's being sent.
"""

import asyncio
import json
import logging
import httpx
from examples.shared import DEFAULT_INIT_PARAMS
from mcp.types import JSONRPCMessage
from mcp.shared.message import SessionMessage
from src.asyncmcp.streamable_http_webhook.client import StreamableHTTPWebhookTransport

# Enable debug logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


async def test_init_request():
    """Test what the initialize request looks like."""

    # Create a sample initialize request
    request_dict = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": DEFAULT_INIT_PARAMS}

    jsonrpc_message = JSONRPCMessage.model_validate(request_dict)
    session_message = SessionMessage(jsonrpc_message)

    # Create transport
    transport = StreamableHTTPWebhookTransport(
        url="http://localhost:8000/mcp", webhook_url="http://localhost:8001/webhook", client_id="test-client-123"
    )

    # Test prepare_initialize_request
    request_data = transport._prepare_initialize_request(session_message)
    headers = transport._prepare_request_headers(transport.request_headers)

    print("=== REQUEST DEBUG ===")
    print("URL:", transport.url)
    print("Headers:", json.dumps(headers, indent=2))
    print("Request Data:", json.dumps(request_data, indent=2))

    # Test actual HTTP request
    print("\n=== ACTUAL HTTP REQUEST ===")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                transport.url,
                json=request_data,
                headers=headers,
            )
            print("Status Code:", response.status_code)
            print("Response Headers:", dict(response.headers))
            print("Response Text:", response.text)

    except Exception as e:
        print("Request failed:", e)


if __name__ == "__main__":
    asyncio.run(test_init_request())
