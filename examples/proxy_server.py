#!/usr/bin/env python3
"""Example proxy server that bridges StreamableHTTP to asyncmcp transports.

This example demonstrates how to set up a proxy server that:
1. Exposes a standard MCP StreamableHTTP endpoint
2. Forwards requests to an asyncmcp backend (SQS, SNS+SQS, Webhook)
3. Streams responses back to the client

Usage:
    # Start proxy with SQS backend
    python proxy_server.py --backend sqs

    # Start proxy with SNS+SQS backend
    python proxy_server.py --backend sns-sqs

    # Start proxy with authentication
    python proxy_server.py --backend sqs --auth-token "secret-token"

    # Start proxy on custom port
    python proxy_server.py --backend sqs --port 9090
"""

import asyncio
import logging
import os
from typing import Optional

import boto3
import click

from asyncmcp.proxy import ProxyConfig, ProxyServer
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.webhook.utils import WebhookClientConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# AWS LocalStack configuration
AWS_CONFIG = {
    "region_name": "us-east-1",
    "endpoint_url": os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}

# Default queue/topic ARNs for LocalStack
DEFAULT_RESOURCES = {
    "sqs_request_queue": "http://localhost:4566/000000000000/mcp-processor",
    "sqs_response_queue": "http://localhost:4566/000000000000/mcp-consumer",
    "sns_topic": "arn:aws:sns:us-east-1:000000000000:mcp-proxy-topic",
    "webhook_server": "http://localhost:8001/mcp/request",
}


def create_sqs_backend_config() -> tuple[SqsClientConfig, dict]:
    """Create SQS backend configuration."""
    sqs_client = boto3.client("sqs", **AWS_CONFIG)

    config = SqsClientConfig(
        read_queue_url=DEFAULT_RESOURCES["sqs_request_queue"],
        response_queue_url=DEFAULT_RESOURCES["sqs_response_queue"],
    )

    backend_clients = {"sqs_client": sqs_client}

    return config, backend_clients


def create_sns_sqs_backend_config() -> tuple[SnsSqsClientConfig, dict]:
    """Create SNS+SQS backend configuration."""
    sqs_client = boto3.client("sqs", **AWS_CONFIG)
    sns_client = boto3.client("sns", **AWS_CONFIG)

    config = SnsSqsClientConfig(
        sns_topic_arn=DEFAULT_RESOURCES["sns_topic"],
        sqs_queue_url=DEFAULT_RESOURCES["sqs_response_queue"],
    )

    backend_clients = {
        "sqs_client": sqs_client,
        "sns_client": sns_client,
    }

    return config, backend_clients


def create_webhook_backend_config() -> tuple[WebhookClientConfig, dict]:
    """Create Webhook backend configuration."""
    config = WebhookClientConfig(
        server_url=DEFAULT_RESOURCES["webhook_server"],
    )

    backend_clients = {}

    return config, backend_clients


@click.command()
@click.option(
    "--backend",
    type=click.Choice(["sqs", "sns-sqs", "webhook"], case_sensitive=False),
    default="sqs",
    help="Backend transport type",
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind the proxy server to",
)
@click.option(
    "--port",
    type=int,
    default=8080,
    help="Port to bind the proxy server to",
)
@click.option(
    "--server-path",
    default="/mcp",
    help="HTTP path for the MCP endpoint",
)
@click.option(
    "--auth-token",
    help="Authentication token (if provided, auth is enabled)",
)
@click.option(
    "--cors-origin",
    multiple=True,
    help="CORS allowed origins (can be specified multiple times)",
)
@click.option(
    "--max-sessions",
    type=int,
    default=100,
    help="Maximum number of concurrent sessions",
)
@click.option(
    "--stateless",
    is_flag=True,
    help="Run in stateless mode",
)
def main(
    backend: str,
    host: str,
    port: int,
    server_path: str,
    auth_token: Optional[str],
    cors_origin: tuple[str, ...],
    max_sessions: int,
    stateless: bool,
):
    """Run the asyncmcp proxy server."""
    print(f"🚀 Starting AsyncMCP Proxy Server")
    print(f"   Backend: {backend}")
    print(f"   Address: http://{host}:{port}{server_path}")

    if auth_token:
        print(f"   Auth: Enabled (token required)")
    else:
        print(f"   Auth: Disabled")

    if cors_origin:
        print(f"   CORS: {', '.join(cors_origin)}")

    print()

    # Create backend configuration
    if backend == "sqs":
        backend_config, backend_clients = create_sqs_backend_config()
        backend_transport = "sqs"
    elif backend == "sns-sqs":
        backend_config, backend_clients = create_sns_sqs_backend_config()
        backend_transport = "sns_sqs"
    elif backend == "webhook":
        backend_config, backend_clients = create_webhook_backend_config()
        backend_transport = "webhook"
    else:
        raise ValueError(f"Unknown backend: {backend}")

    # Create proxy configuration
    proxy_config = ProxyConfig(
        host=host,
        port=port,
        server_path=server_path,
        backend_transport=backend_transport,
        backend_config=backend_config,
        backend_clients=backend_clients,
        max_sessions=max_sessions,
        stateless=stateless,
        auth_enabled=bool(auth_token),
        auth_token=auth_token,
        cors_origins=list(cors_origin) if cors_origin else None,
    )

    # Create and run proxy server
    proxy_server = ProxyServer(proxy_config)

    print("📡 Proxy server is ready to accept connections")
    print(f"   MCP endpoint: http://{host}:{port}{server_path}")
    print(f"   Health endpoint: http://{host}:{port}/health")
    print()
    print("ℹ️  The MCP endpoint handles both:")
    print("   - GET requests for SSE connections")
    print("   - POST requests for messages")
    print()
    print("Press Ctrl+C to stop the server")

    try:
        asyncio.run(proxy_server.run())
    except KeyboardInterrupt:
        print("\n👋 Shutting down proxy server")


if __name__ == "__main__":
    main()
