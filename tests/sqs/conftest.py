"""Configuration and shared fixtures for SQS transport tests."""

from tests.sqs.shared_fixtures import (
    client_response_queue_url,
    client_server_config,
    client_transport_config,
    mock_mcp_server,
    mock_sqs_client,
    sample_initialize_sqs_message,
    sample_jsonrpc_initialize_request,
    sample_jsonrpc_notification,
    sample_jsonrpc_request,
    sample_jsonrpc_response,
    sample_sqs_message,
    server_transport_config,
)

__all__ = [
    "mock_sqs_client",
    "sample_jsonrpc_request",
    "sample_jsonrpc_initialize_request",
    "sample_jsonrpc_response",
    "sample_jsonrpc_notification",
    "sample_sqs_message",
    "sample_initialize_sqs_message",
    "client_transport_config",
    "client_response_queue_url",
    "server_transport_config",
    "client_server_config",
    "mock_mcp_server",
]
