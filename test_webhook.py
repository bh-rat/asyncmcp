#!/usr/bin/env python3
"""
Simple test script to demonstrate webhook functionality
"""

import asyncio
import time
import httpx
import json
from shared import print_colored


async def test_webhook_endpoint():
    """Test webhook server endpoints"""
    print_colored("🧪 Testing webhook endpoints...", "cyan")
    
    # Test server health
    async with httpx.AsyncClient() as client:
        try:
            # Test initialize request
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                    "_meta": {"webhookUrl": "http://localhost:8001/webhook/response"}
                }
            }
            
            response = await client.post(
                "http://localhost:8000/mcp/request",
                json=init_request,
                headers={"X-Client-ID": "test-client"}
            )
            
            print_colored(f"✅ Initialize request: {response.status_code}", "green")
            print_colored(f"   Response: {response.text}", "white")
            
            # Test tools request
            tools_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            }
            
            response = await client.post(
                "http://localhost:8000/mcp/request",
                json=tools_request,
                headers={"X-Client-ID": "test-client", "X-Session-ID": "test-session"}
            )
            
            print_colored(f"✅ Tools request: {response.status_code}", "green")
            print_colored(f"   Response: {response.text}", "white")
            
        except Exception as e:
            print_colored(f"❌ Error: {e}", "red")


async def main():
    """Run the webhook tests"""
    print_colored("🚀 Starting webhook transport tests", "cyan")
    
    # Wait for server to be ready
    await asyncio.sleep(2)
    
    await test_webhook_endpoint()
    
    print_colored("✅ Webhook transport tests completed", "green")


if __name__ == "__main__":
    asyncio.run(main()) 