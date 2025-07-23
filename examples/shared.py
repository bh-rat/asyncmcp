#!/usr/bin/env python3
"""
Shared utilities for examples.
"""

import os
import json
import time
import logging

from typing import Dict, Any, Optional, List, Tuple, Union
import anyio
import mcp.types as types
from mcp.shared.message import SessionMessage
import boto3

from asyncmcp.sqs.utils import SqsTransportConfig
from asyncmcp import SnsSqsServerConfig, SnsSqsClientConfig

# AWS LocalStack configuration
AWS_CONFIG = {
    "region_name": "us-east-1",
    "endpoint_url": "http://localhost:4566",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}
# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)


# Resource ARNs and URLs for LocalStack
RESOURCES = {
    "client_request_topic": "arn:aws:sns:us-east-1:000000000000:mcp-requests",
    "server_response_topic": "arn:aws:sns:us-east-1:000000000000:mcp-response",
    "client_response_queue": "http://localhost:4566/000000000000/mcp-consumer",
    "server_request_queue": "http://localhost:4566/000000000000/mcp-processor",
}

logger = logging.getLogger(__name__)
# Transport types
TRANSPORT_SNS_SQS = "sns-sqs"
TRANSPORT_SQS = "sqs"
# Common MCP configuration
DEFAULT_INIT_PARAMS = {
    "protocolVersion": "2025‑06‑18",
    "capabilities": {"roots": {"listChanged": True}},
    "clientInfo": {"name": "mcp-client", "version": "1.0.0"},
}

DEFAULT_SERVER_INFO = {"name": "mcp-transport-test-server", "version": "1.0.0"}


def setup_aws_clients():
    """Setup AWS clients for LocalStack"""
    sqs_client = boto3.client("sqs", **AWS_CONFIG)
    sns_client = boto3.client("sns", **AWS_CONFIG)
    return sqs_client, sns_client


def setup_logging(name: str, level: int = logging.INFO):
    """Setup logging for CLI applications"""
    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")
    return logging.getLogger(name)


def print_colored(text: str, color: str = "white"):
    """Print colored text to console"""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m",
    }

    color_code = colors.get(color, colors["white"])
    reset_code = colors["reset"]
    print(f"{color_code}{text}{reset_code}")


def print_json(data: Dict[str, Any], title: str = ""):
    """Pretty print JSON data"""
    if title:
        print_colored(f"\n📋 {title}:", "cyan")
    print_colored(json.dumps(data, indent=2), "white")


def create_sns_sqs_server_config(
    server_sqs_queue_name: str = "mcp-server-requests",
    server_sns_topic_name: str = "mcp-server-requests",
    region_name: str = "us-east-1",
    endpoint_url: str = "http://localhost:4566",
    max_messages: int = 10,
    wait_time_seconds: int = 20,
    poll_interval_seconds: float = 1.0,
) -> Tuple[SnsSqsServerConfig, Any, Any]:
    """
    Create SNS/SQS server configuration with AWS clients.

    Args:
        server_sqs_queue_name: Name of the server's SQS queue
        server_sns_topic_name: Name of the server's SNS topic (for receiving client requests)
        region_name: AWS region name
        endpoint_url: LocalStack endpoint URL
        max_messages: Maximum messages to process per batch
        wait_time_seconds: Long polling wait time
        poll_interval_seconds: Polling interval between batches

    Returns:
        Tuple of (config, sqs_client, sns_client)
    """
    # Create AWS clients for LocalStack
    sqs_client = boto3.client(
        "sqs",
        region_name=region_name,
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    sns_client = boto3.client(
        "sns",
        region_name=region_name,
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    # Get or create server SQS queue
    try:
        queue_response = sqs_client.get_queue_url(QueueName=server_sqs_queue_name)
        server_sqs_queue_url = queue_response["QueueUrl"]
        logger.info(f"Using existing server SQS queue: {server_sqs_queue_url}")
    except sqs_client.exceptions.QueueDoesNotExist:
        queue_response = sqs_client.create_queue(QueueName=server_sqs_queue_name)
        server_sqs_queue_url = queue_response["QueueUrl"]
        logger.info(f"Created new server SQS queue: {server_sqs_queue_url}")

    # Get or create server SNS topic for receiving client requests
    try:
        topics_response = sns_client.list_topics()
        server_topic_arn = None
        for topic in topics_response.get("Topics", []):
            if server_sns_topic_name in topic["TopicArn"]:
                server_topic_arn = topic["TopicArn"]
                break

        if not server_topic_arn:
            topic_response = sns_client.create_topic(Name=server_sns_topic_name)
            server_topic_arn = topic_response["TopicArn"]
            logger.info(f"Created new server SNS topic: {server_topic_arn}")
        else:
            logger.info(f"Using existing server SNS topic: {server_topic_arn}")

        # Subscribe the server's SQS queue to the server's SNS topic
        try:
            # Get queue ARN for subscription
            queue_attributes = sqs_client.get_queue_attributes(
                QueueUrl=server_sqs_queue_url, AttributeNames=["QueueArn"]
            )
            queue_arn = queue_attributes["Attributes"]["QueueArn"]

            subscription_response = sns_client.subscribe(TopicArn=server_topic_arn, Protocol="sqs", Endpoint=queue_arn)
            logger.info(f"Subscribed server SQS queue to server topic: {subscription_response['SubscriptionArn']}")

            # Set queue policy to allow SNS to send messages
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "sns.amazonaws.com"},
                        "Action": "sqs:SendMessage",
                        "Resource": queue_arn,
                        "Condition": {"ArnEquals": {"aws:SourceArn": server_topic_arn}},
                    }
                ],
            }

            sqs_client.set_queue_attributes(QueueUrl=server_sqs_queue_url, Attributes={"Policy": json.dumps(policy)})
            logger.info(f"Set server queue policy to allow SNS messages from topic: {server_topic_arn}")

        except Exception as e:
            logger.warning(f"Error setting up server SNS-SQS subscription: {e}")

    except Exception as e:
        logger.error(f"Error handling server SNS topic: {e}")
        topic_response = sns_client.create_topic(Name=server_sns_topic_name)
        server_topic_arn = topic_response["TopicArn"]

    # Create server configuration
    config = SnsSqsServerConfig(
        sqs_queue_url=server_sqs_queue_url,
        max_messages=max_messages,
        wait_time_seconds=wait_time_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    return config, sqs_client, sns_client


def get_client_response_queue_url() -> str:
    """Get the client's response queue URL for dynamic queue configuration."""
    return RESOURCES["client_response_queue"]


async def send_mcp_request(write_stream, method: str, params: dict = None, request_id: int = 1) -> SessionMessage:
    """Send an MCP request and return the SessionMessage"""
    request_dict = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}

    jsonrpc_message = types.JSONRPCMessage.model_validate(request_dict)
    session_message = SessionMessage(jsonrpc_message)

    print_colored(f"📤 Sending {method} request...", "blue")
    await write_stream.send(session_message)

    # allowing messages to flush
    await anyio.sleep(2)

    return session_message


async def wait_for_response(read_stream, timeout: float = 500.0):
    """Wait for a response from the stream"""
    try:
        with anyio.move_on_after(timeout) as cancel_scope:
            response = await read_stream.receive()

            if isinstance(response, Exception):
                print_colored(f"❌ Exception: {response}", "red")
                return None
            return response

            if cancel_scope.cancelled_caught:
                print_colored(f"⏰ Request timeout ({timeout}s)", "red")
                return None

    except Exception as e:
        print_colored(f"❌ Error waiting for response: {e}", "red")
        return None


async def send_request_and_wait(
    write_stream, read_stream, method: str, params: dict = None, request_id: int = 1, timeout: float = 500.0
):
    """Send a request and wait for response"""
    await send_mcp_request(write_stream, method, params, request_id)

    response = await wait_for_response(read_stream, timeout)
    if not response:
        return False

    message = response.message.root
    if hasattr(message, "result"):
        print_colored(f"✅ {method} successful!", "green")
        return message.result
    elif hasattr(message, "error"):
        print_colored(f"❌ {method} error: {message.error}", "red")
        return False
    else:
        print_colored(f"❌ Unexpected response format", "red")
        return False


def create_json_rpc_request(method: str, params: Dict[str, Any], request_id: int = 1) -> Dict[str, Any]:
    """Create a JSON-RPC request"""
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def create_json_rpc_response(
    request_id: int, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Create a JSON-RPC response"""
    response = {"jsonrpc": "2.0", "id": request_id}

    if error:
        response["error"] = error
    else:
        response["result"] = result or {}

    return response


def create_json_rpc_notification(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a JSON-RPC notification"""
    return {"jsonrpc": "2.0", "method": method, "params": params}


def send_to_sns(sns_client, topic_arn: str, message: Dict[str, Any], message_type: str = "MCP-JSONRPC"):
    """Send a message to SNS topic"""
    json_message = json.dumps(message)

    message_attributes = {"MessageType": {"DataType": "String", "StringValue": message_type}}

    response = sns_client.publish(TopicArn=topic_arn, Message=json_message, MessageAttributes=message_attributes)

    return response


def receive_from_sqs(sqs_client, queue_url: str, wait_time: int = 5, max_messages: int = 1):
    """Receive messages from SQS queue"""
    response = sqs_client.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=max_messages, WaitTimeSeconds=wait_time, MessageAttributeNames=["All"]
    )
    messages = []
    for sqs_message in response.get("Messages", []):
        try:
            # Parse message body (handle SNS notification format)
            message_body = sqs_message["Body"]

            # Handle SNS notification format
            try:
                sns_message = json.loads(message_body)
                if "Message" in sns_message and "Type" in sns_message:
                    actual_message_body = sns_message["Message"]
                else:
                    actual_message_body = message_body
            except json.JSONDecodeError:
                actual_message_body = message_body

            # Parse the JSON-RPC message
            jsonrpc_message = json.loads(actual_message_body)
            messages.append({"message": jsonrpc_message, "receipt_handle": sqs_message["ReceiptHandle"]})

        except (json.JSONDecodeError, KeyError) as e:
            logging.error(f"Error parsing message: {e}")
            continue

    return messages


def delete_sqs_message(sqs_client, queue_url: str, receipt_handle: str):
    """Delete a message from SQS queue"""
    sqs_client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def print_test_results(test_results: List[tuple], title: str = "Test Results"):
    """Print formatted test results"""
    print_colored("\n" + "=" * 50, "white")
    print_colored(f"🎯 {title}", "cyan")
    print_colored("=" * 50, "white")

    passed = sum(1 for _, success in test_results if success)
    total = len(test_results)

    for test_name, success in test_results:
        icon = "✅" if success else "❌"
        print_colored(f"{icon} {test_name}: {'PASSED' if success else 'FAILED'}", "green" if success else "red")
    print_colored(f"\n🏆 Overall: {passed}/{total} tests passed", "green" if passed == total else "yellow")

    return passed == total


def format_duration(seconds: float) -> str:
    """Format duration in human readable format"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


# Common tool implementations
def echo_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    """Echo tool - returns the input message"""
    message = params.get("message", "")
    return {"echo": message, "timestamp": time.time()}


def create_sns_sqs_client_config(
    client_sqs_queue_name: str = "mcp-client-responses",
    server_sns_topic_name: str = "mcp-server-requests",
    client_id: str = "example-client",
    region_name: str = "us-east-1",
    endpoint_url: str = "http://localhost:4566",
    max_messages: int = 10,
    wait_time_seconds: int = 20,
    poll_interval_seconds: float = 1.0,
) -> Tuple[SnsSqsClientConfig, Any, Any, str]:
    """
    Create SNS/SQS client configuration with AWS clients.

    Returns:
        Tuple of (config, sqs_client, sns_client, client_topic_arn)
    """
    # Create AWS clients for LocalStack
    sqs_client = boto3.client(
        "sqs",
        region_name=region_name,
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    sns_client = boto3.client(
        "sns",
        region_name=region_name,
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    # Get or create client SQS queue for responses
    try:
        queue_response = sqs_client.get_queue_url(QueueName=client_sqs_queue_name)
        client_sqs_queue_url = queue_response["QueueUrl"]
        logger.info(f"Using existing client SQS queue: {client_sqs_queue_url}")
    except sqs_client.exceptions.QueueDoesNotExist:
        queue_response = sqs_client.create_queue(QueueName=client_sqs_queue_name)
        client_sqs_queue_url = queue_response["QueueUrl"]
        logger.info(f"Created new client SQS queue: {client_sqs_queue_url}")

    # Get or create server SNS topic for client requests
    try:
        topics_response = sns_client.list_topics()
        server_topic_arn = None
        for topic in topics_response.get("Topics", []):
            if server_sns_topic_name in topic["TopicArn"]:
                server_topic_arn = topic["TopicArn"]
                break

        if not server_topic_arn:
            topic_response = sns_client.create_topic(Name=server_sns_topic_name)
            server_topic_arn = topic_response["TopicArn"]
            logger.info(f"Created new server SNS topic: {server_topic_arn}")
        else:
            logger.info(f"Using existing server SNS topic: {server_topic_arn}")
    except Exception as e:
        logger.error(f"Error handling SNS topic: {e}")
        topic_response = sns_client.create_topic(Name=server_sns_topic_name)
        server_topic_arn = topic_response["TopicArn"]

    # Create client response topic (where server sends responses back to this client)
    client_topic_name = f"mcp-client-{client_id}-responses"
    try:
        client_topic_response = sns_client.create_topic(Name=client_topic_name)
        client_topic_arn = client_topic_response["TopicArn"]
        logger.info(f"Created new client response topic: {client_topic_arn}")

        # Subscribe the client's SQS queue to the client's response topic
        # Convert the LocalStack URL to the ARN format that SNS expects
        queue_attributes = sqs_client.get_queue_attributes(QueueUrl=client_sqs_queue_url, AttributeNames=["QueueArn"])
        queue_arn = queue_attributes["Attributes"]["QueueArn"]

        subscription_response = sns_client.subscribe(TopicArn=client_topic_arn, Protocol="sqs", Endpoint=queue_arn)
        logger.info(f"Subscribed client SQS queue to response topic: {subscription_response['SubscriptionArn']}")

        # Set queue policy to allow SNS to send messages
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "sns.amazonaws.com"},
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn,
                    "Condition": {"ArnEquals": {"aws:SourceArn": client_topic_arn}},
                }
            ],
        }

        sqs_client.set_queue_attributes(QueueUrl=client_sqs_queue_url, Attributes={"Policy": json.dumps(policy)})
        logger.info(f"Set queue policy to allow SNS messages from topic: {client_topic_arn}")

    except Exception as e:
        logger.error(f"Error creating client topic and subscription: {e}")
        raise

    # Create client configuration
    config = SnsSqsClientConfig(
        sqs_queue_url=client_sqs_queue_url,
        sns_topic_arn=server_topic_arn,
        client_id=client_id,
        max_messages=max_messages,
        wait_time_seconds=wait_time_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    return config, sqs_client, sns_client, client_topic_arn


def create_sqs_config(
    queue_name: str = "mcp-processor",
    response_queue_name: str = "mcp-consumer",
    region_name: str = "us-east-1",
    endpoint_url: str = "http://localhost:4566",
    max_messages: int = 10,
    wait_time_seconds: int = 20,
    poll_interval_seconds: float = 1.0,
) -> Union[Tuple[SnsSqsServerConfig, Any, Any], Tuple[SqsTransportConfig, Any, None]]:
    """
    Create SQS configuration with AWS clients.

    Args:
        queue_name: Name of the main SQS queue
        response_queue_name: Name of the response queue
        region_name: AWS region name
        endpoint_url: LocalStack endpoint URL
        max_messages: Maximum messages to process per batch
        wait_time_seconds: Long polling wait time
        poll_interval_seconds: Polling interval between batches

    Returns:
        Tuple of (config, sqs_client, sns_client) - sns_client is None for SQS-only
    """
    # Create AWS SQS client for LocalStack
    sqs_client = boto3.client(
        "sqs",
        region_name=region_name,
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

    # Get or create main SQS queue
    queue_response = sqs_client.get_queue_url(QueueName=queue_name)
    queue_url = queue_response["QueueUrl"]
    logger.info(f"Using existing SQS queue: {queue_url}")

    response_queue_response = sqs_client.get_queue_url(QueueName=response_queue_name)
    response_queue_url = response_queue_response["QueueUrl"]
    logger.info(f"Using existing response SQS queue: {response_queue_url}")

    # Create configuration
    config = SqsTransportConfig(
        read_queue_url=queue_url,
        response_queue_url=response_queue_url,
        max_messages=max_messages,
        wait_time_seconds=wait_time_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    return config, sqs_client, None


async def cleanup_aws_resources(
    sqs_client: Any, sns_client: Any = None, queue_names: list = None, topic_names: list = None
):
    """
    Clean up AWS resources used in examples.

    Args:
        sqs_client: boto3 SQS client
        sns_client: boto3 SNS client (optional)
        queue_names: List of SQS queue names to delete
        topic_names: List of SNS topic names to delete
    """
    if queue_names:
        for queue_name in queue_names:
            try:
                queue_response = sqs_client.get_queue_url(QueueName=queue_name)
                sqs_client.delete_queue(QueueUrl=queue_response["QueueUrl"])
                logger.info(f"Deleted SQS queue: {queue_name}")
            except Exception as e:
                logger.warning(f"Could not delete SQS queue {queue_name}: {e}")

    if sns_client and topic_names:
        topics_response = sns_client.list_topics()
        for topic_name in topic_names:
            for topic in topics_response.get("Topics", []):
                if topic_name in topic["TopicArn"]:
                    try:
                        sns_client.delete_topic(TopicArn=topic["TopicArn"])
                        logger.info(f"Deleted SNS topic: {topic_name}")
                    except Exception as e:
                        logger.warning(f"Could not delete SNS topic {topic_name}: {e}")
                    break


def setup_localstack_env():
    """
    Set up environment variables for LocalStack.
    """
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def setup_logging(level: str = "INFO"):
    """
    Set up logging configuration.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,  # Override any existing configuration
    )
