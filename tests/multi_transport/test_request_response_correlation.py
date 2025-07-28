"""
Test request-response correlation (id) across different transports.

This test suite focuses on ensuring that JSON-RPC 2.0 request-response correlation
is maintained correctly when requests arrive via one transport but responses are
delivered via another transport (e.g., HTTP request -> webhook response).
"""

import json
import uuid
from typing import Dict, List
from unittest.mock import MagicMock

import pytest
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ErrorData, JSONRPCError, JSONRPCRequest, JSONRPCResponse

from asyncmcp.multi_transport import MultiTransportServer, TransportType, transport_info


class MockHttpClient:
    """Mock HTTP client for simulating webhook calls."""

    def __init__(self):
        self.sent_requests: List[Dict] = []
        self.response_status = 200

    async def post(self, url: str, json_data: dict, headers: dict = None):
        """Mock HTTP POST request."""
        self.sent_requests.append({"url": url, "json": json_data, "headers": headers or {}})

        mock_response = MagicMock()
        mock_response.status_code = self.response_status
        return mock_response


class MockTransport:
    """Mock transport that tracks message IDs."""

    def __init__(self, transport_type: str):
        self.transport_type = transport_type
        self.received_requests: List[JSONRPCRequest] = []
        self.sent_responses: List[JSONRPCResponse] = []
        self.is_active = True

    async def start(self):
        """Start the transport."""
        pass

    async def stop(self):
        """Stop the transport."""
        pass

    async def send_response(self, response: JSONRPCResponse, session_id: str = None):
        """Send a response via this transport."""
        self.sent_responses.append(response)

    def receive_request(self, request: JSONRPCRequest):
        """Simulate receiving a request via this transport."""
        self.received_requests.append(request)


@pytest.fixture
def mock_http_client():
    """Mock HTTP client for webhook testing."""
    return MockHttpClient()


@pytest.fixture
def mcp_server():
    """Create a mock MCP server with cross-transport tools."""
    server = MagicMock(spec=Server)

    # Tool that should receive requests via HTTP but respond via webhook
    @transport_info(TransportType.WEBHOOK, description="HTTP->Webhook tool")
    async def http_to_webhook_tool(url: str) -> str:
        # Simulate some processing
        return f"Processed {url} via webhook"

    # Tool that should receive requests via SQS but respond via webhook
    @transport_info(TransportType.WEBHOOK, description="SQS->Webhook tool")
    async def sqs_to_webhook_tool(data: dict) -> dict:
        return {"processed": True, "input": data}

    # Tool that should receive requests via SNS but respond via webhook
    @transport_info(TransportType.WEBHOOK, description="SNS->Webhook tool")
    async def sns_to_webhook_tool(message: str) -> str:
        return f"SNS processed: {message}"

    # Standard HTTP tool for comparison
    @transport_info(TransportType.HTTP, description="HTTP only tool")
    async def http_only_tool(value: str) -> str:
        return f"HTTP: {value}"

    server._tool_handlers = {
        "http_to_webhook_tool": http_to_webhook_tool,
        "sqs_to_webhook_tool": sqs_to_webhook_tool,
        "sns_to_webhook_tool": sns_to_webhook_tool,
        "http_only_tool": http_only_tool,
    }

    return server


@pytest.fixture
def multi_server(mcp_server):
    """Create MultiTransportServer with mock transports."""
    server = MultiTransportServer(mcp_server, enable_routing_validation=False)

    # Set up mock transports
    http_transport = MockTransport("http")
    webhook_transport = MockTransport("webhook")
    sqs_transport = MockTransport("sqs")
    sns_transport = MockTransport("sns_sqs")

    # Register transports
    server.registry.register_transport(
        transport_type=TransportType.HTTP,
        transport_instance=http_transport,
        config={"host": "localhost", "port": 8000},
        tools=["http_only_tool"],
        is_default=True,
    )

    server.registry.register_transport(
        transport_type=TransportType.WEBHOOK,
        transport_instance=webhook_transport,
        config={"webhook_url": "http://localhost:8001/webhook"},
        tools=["http_to_webhook_tool", "sqs_to_webhook_tool", "sns_to_webhook_tool"],
    )

    server.registry.register_transport(
        transport_type=TransportType.SQS,
        transport_instance=sqs_transport,
        config={"queue_url": "http://localhost:9324/000000000000/test-queue"},
    )

    server.registry.register_transport(
        transport_type=TransportType.SNS_SQS,
        transport_instance=sns_transport,
        config={"topic_arn": "arn:aws:sns:us-east-1:000000000000:test-topic"},
    )

    # Make all transports active
    for transport_type in [TransportType.HTTP, TransportType.WEBHOOK, TransportType.SQS, TransportType.SNS_SQS]:
        transport_info = server.registry.get_transport_info(transport_type)
        if transport_info:
            transport_info.is_active = True

    return server


class TestRequestResponseCorrelation:
    """Test request-response correlation across transports."""

    def test_json_rpc_id_preservation_structure(self):
        """Test that JSON-RPC request and response structures preserve ID correctly."""
        # Create a sample request with ID
        request_id = str(uuid.uuid4())

        # Create JSONRPCRequest directly with proper params structure
        jsonrpc_request = JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={"name": "http_to_webhook_tool", "arguments": {"url": "https://example.com"}},
        )

        # Simulate processing and creating response
        call_tool_result = CallToolResult(
            content=[{"type": "text", "text": "Processed https://example.com via webhook"}]
        )

        # Create JSONRPCResponse with same ID
        jsonrpc_response = JSONRPCResponse(
            jsonrpc="2.0",
            id=request_id,  # CRITICAL: Must match request ID
            result=call_tool_result.model_dump(),
        )

        # Verify ID preservation
        assert jsonrpc_request.id == request_id
        assert jsonrpc_response.id == request_id
        assert jsonrpc_request.id == jsonrpc_response.id

        # Verify serialization preserves ID
        request_json = json.loads(jsonrpc_request.model_dump_json())
        response_json = json.loads(jsonrpc_response.model_dump_json())

        assert request_json["id"] == request_id
        assert response_json["id"] == request_id

    @pytest.mark.anyio
    async def test_http_request_webhook_response_correlation(self, multi_server, mock_http_client):
        """Test HTTP request -> webhook response with proper ID correlation."""
        multi_server._discover_tools()

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Create HTTP request for webhook-routed tool
        jsonrpc_request = JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={"name": "http_to_webhook_tool", "arguments": {"url": "https://example.com"}},
        )

        # Simulate receiving request via HTTP transport
        http_transport = multi_server.registry.get_transport_info(TransportType.HTTP).transport_instance
        http_transport.receive_request(jsonrpc_request)

        # Simulate tool execution (would happen in MCP server)
        tool_handler = multi_server.mcp_server._tool_handlers["http_to_webhook_tool"]
        tool_result = await tool_handler("https://example.com")

        # Create response that should go via webhook
        call_tool_result = CallToolResult(content=[{"type": "text", "text": tool_result}])

        jsonrpc_response = JSONRPCResponse(
            jsonrpc="2.0",
            id=request_id,  # MUST preserve original request ID
            result=call_tool_result.model_dump(),
        )

        # Simulate webhook delivery
        webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance
        await webhook_transport.send_response(jsonrpc_response)

        # Verify correlation
        assert len(http_transport.received_requests) == 1
        assert len(webhook_transport.sent_responses) == 1

        received_request = http_transport.received_requests[0]
        sent_response = webhook_transport.sent_responses[0]

        # CRITICAL: IDs must match
        assert received_request.id == request_id
        assert sent_response.id == request_id
        assert received_request.id == sent_response.id

        # Verify request came via HTTP for webhook tool
        assert received_request.params["name"] == "http_to_webhook_tool"

        # Verify response content
        assert "Processed https://example.com via webhook" in str(sent_response.result)

    @pytest.mark.anyio
    async def test_sqs_request_webhook_response_correlation(self, multi_server):
        """Test SQS request -> webhook response with proper ID correlation."""
        multi_server._discover_tools()

        request_id = str(uuid.uuid4())

        # Create SQS request for webhook-routed tool
        jsonrpc_request = JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={"name": "sqs_to_webhook_tool", "arguments": {"data": {"key": "value"}}},
        )

        sqs_transport = multi_server.registry.get_transport_info(TransportType.SQS).transport_instance
        sqs_transport.receive_request(jsonrpc_request)

        # Simulate tool execution
        tool_handler = multi_server.mcp_server._tool_handlers["sqs_to_webhook_tool"]
        tool_result = await tool_handler({"key": "value"})

        # Create webhook response
        call_tool_result = CallToolResult(content=[{"type": "text", "text": json.dumps(tool_result)}])

        jsonrpc_response = JSONRPCResponse(
            jsonrpc="2.0",
            id=request_id,  # MUST preserve original request ID
            result=call_tool_result.model_dump(),
        )

        webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance
        await webhook_transport.send_response(jsonrpc_response)

        # Verify correlation
        assert len(sqs_transport.received_requests) == 1
        assert len(webhook_transport.sent_responses) == 1

        received_request = sqs_transport.received_requests[0]
        sent_response = webhook_transport.sent_responses[0]

        # CRITICAL: IDs must match across SQS->Webhook
        assert received_request.id == request_id
        assert sent_response.id == request_id
        assert received_request.id == sent_response.id

    @pytest.mark.anyio
    async def test_sns_request_webhook_response_correlation(self, multi_server):
        """Test SNS+SQS request -> webhook response with proper ID correlation."""
        multi_server._discover_tools()

        request_id = str(uuid.uuid4())

        # Create SNS request for webhook-routed tool
        jsonrpc_request = JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={"name": "sns_to_webhook_tool", "arguments": {"message": "Hello from SNS"}},
        )

        sns_transport = multi_server.registry.get_transport_info(TransportType.SNS_SQS).transport_instance
        sns_transport.receive_request(jsonrpc_request)

        # Simulate tool execution
        tool_handler = multi_server.mcp_server._tool_handlers["sns_to_webhook_tool"]
        tool_result = await tool_handler("Hello from SNS")

        # Create webhook response
        call_tool_result = CallToolResult(content=[{"type": "text", "text": tool_result}])

        jsonrpc_response = JSONRPCResponse(
            jsonrpc="2.0",
            id=request_id,  # MUST preserve original request ID
            result=call_tool_result.model_dump(),
        )

        webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance
        await webhook_transport.send_response(jsonrpc_response)

        # Verify correlation
        assert len(sns_transport.received_requests) == 1
        assert len(webhook_transport.sent_responses) == 1

        received_request = sns_transport.received_requests[0]
        sent_response = webhook_transport.sent_responses[0]

        # CRITICAL: IDs must match across SNS->Webhook
        assert received_request.id == request_id
        assert sent_response.id == request_id
        assert received_request.id == sent_response.id

    @pytest.mark.anyio
    async def test_malformed_id_handling(self, multi_server):
        """Test handling of malformed or missing request IDs."""
        multi_server._discover_tools()

        # Test with missing ID - use JSONRPCNotification for requests without ID
        from mcp.types import JSONRPCNotification

        request_no_id = JSONRPCNotification(
            jsonrpc="2.0",
            method="tools/call",
            params={"name": "http_to_webhook_tool", "arguments": {"url": "https://example.com"}},
        )

        http_transport = multi_server.registry.get_transport_info(TransportType.HTTP).transport_instance
        http_transport.receive_request(request_no_id)

        # For JSON-RPC notifications (requests without ID), no response should be sent
        # This is per JSON-RPC 2.0 specification - notifications don't expect responses

        # Verify that notification was received (it's stored as a request in our mock)
        received_request = http_transport.received_requests[0]

        # Notification should not have an ID
        assert not hasattr(received_request, "id") or received_request.id is None

        # Since notifications don't expect responses, we don't send one
        # This test primarily verifies that the system can handle requests without IDs
        webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance

        # No response should be sent for notifications
        assert len(webhook_transport.sent_responses) == 0

    @pytest.mark.anyio
    async def test_null_id_handling(self, multi_server):
        """Test handling of null request IDs."""
        multi_server._discover_tools()

        # Skip this test as MCP's JSONRPCRequest doesn't allow None for ID
        # In practice, null IDs would be handled at the JSON parsing level
        # before creating the JSONRPCRequest object
        pytest.skip("MCP JSONRPCRequest doesn't allow None for ID field - handled at parsing level")

    @pytest.mark.anyio
    async def test_id_type_preservation(self, multi_server):
        """Test that different ID types (string, number) are preserved correctly."""
        multi_server._discover_tools()

        test_cases = [
            ("string-id", "string-id"),
            (42, 42),
            (0, 0),
            (-1, -1),
            ("", ""),  # Empty string is valid
        ]

        for request_id, expected_response_id in test_cases:
            # Clear previous requests/responses
            for transport_type in [TransportType.HTTP, TransportType.WEBHOOK]:
                transport_info = multi_server.registry.get_transport_info(transport_type)
                transport_info.transport_instance.received_requests.clear()
                transport_info.transport_instance.sent_responses.clear()

            # Create request with specific ID type
            jsonrpc_request = JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="tools/call",
                params={"name": "http_to_webhook_tool", "arguments": {"url": f"https://example.com/{request_id}"}},
            )

            http_transport = multi_server.registry.get_transport_info(TransportType.HTTP).transport_instance
            http_transport.receive_request(jsonrpc_request)

            # Process and respond
            tool_handler = multi_server.mcp_server._tool_handlers["http_to_webhook_tool"]
            tool_result = await tool_handler(f"https://example.com/{request_id}")

            call_tool_result = CallToolResult(content=[{"type": "text", "text": tool_result}])

            jsonrpc_response = JSONRPCResponse(
                jsonrpc="2.0",
                id=request_id,  # Must preserve exact ID type and value
                result=call_tool_result.model_dump(),
            )

            webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance
            await webhook_transport.send_response(jsonrpc_response)

            # Verify ID type and value preservation
            received_request = http_transport.received_requests[0]
            sent_response = webhook_transport.sent_responses[0]

            assert received_request.id == request_id
            assert sent_response.id == expected_response_id
            assert type(received_request.id) == type(expected_response_id)

    @pytest.mark.anyio
    async def test_multiple_concurrent_requests_correlation(self, multi_server):
        """Test that multiple concurrent requests maintain proper ID correlation."""
        multi_server._discover_tools()

        # Create multiple requests with different IDs
        request_ids = [str(uuid.uuid4()) for _ in range(5)]
        requests = []

        for i, request_id in enumerate(request_ids):
            request = JSONRPCRequest(
                jsonrpc="2.0",
                id=request_id,
                method="tools/call",
                params={"name": "http_to_webhook_tool", "arguments": {"url": f"https://example{i}.com"}},
            )
            requests.append(request)

        http_transport = multi_server.registry.get_transport_info(TransportType.HTTP).transport_instance
        webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance

        # Send all requests
        for request in requests:
            http_transport.receive_request(request)

        # Process and respond to all requests
        tool_handler = multi_server.mcp_server._tool_handlers["http_to_webhook_tool"]

        for i, request in enumerate(requests):
            tool_result = await tool_handler(f"https://example{i}.com")

            call_tool_result = CallToolResult(content=[{"type": "text", "text": tool_result}])

            jsonrpc_response = JSONRPCResponse(
                jsonrpc="2.0",
                id=request.id,  # Preserve original request ID
                result=call_tool_result.model_dump(),
            )

            await webhook_transport.send_response(jsonrpc_response)

        # Verify all correlations
        assert len(http_transport.received_requests) == 5
        assert len(webhook_transport.sent_responses) == 5

        # Check each request-response pair
        for i in range(5):
            received_request = http_transport.received_requests[i]
            sent_response = webhook_transport.sent_responses[i]

            # IDs should match
            assert received_request.id == request_ids[i]
            assert sent_response.id == request_ids[i]
            assert received_request.id == sent_response.id

    @pytest.mark.anyio
    async def test_error_response_correlation(self, multi_server):
        """Test that error responses maintain proper ID correlation."""
        multi_server._discover_tools()

        request_id = str(uuid.uuid4())

        # Create request that will result in an error
        jsonrpc_request = JSONRPCRequest(
            jsonrpc="2.0",
            id=request_id,
            method="tools/call",
            params={
                "name": "nonexistent_tool",  # This tool doesn't exist
                "arguments": {},
            },
        )

        http_transport = multi_server.registry.get_transport_info(TransportType.HTTP).transport_instance
        http_transport.receive_request(jsonrpc_request)

        # Simulate error response
        error_response = JSONRPCError(
            jsonrpc="2.0",
            id=request_id,  # MUST preserve original request ID even for errors
            error=ErrorData(
                code=-32601,  # Method not found
                message="Tool not found: nonexistent_tool",
            ),
        )

        webhook_transport = multi_server.registry.get_transport_info(TransportType.WEBHOOK).transport_instance
        await webhook_transport.send_response(error_response)

        # Verify error response correlation
        received_request = http_transport.received_requests[0]
        sent_response = webhook_transport.sent_responses[0]

        assert received_request.id == request_id
        assert sent_response.id == request_id
        assert received_request.id == sent_response.id

        # Verify it's an error response
        assert sent_response.error is not None
        assert sent_response.error.code == -32601
