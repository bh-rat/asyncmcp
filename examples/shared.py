#!/usr/bin/env python3
"""
Shared utilities for local CLI testing
"""

import anyio
import boto3
import json
import logging
import sys
import os
import subprocess
import time
import requests

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
import mcp.types as types
from mcp.shared.message import SessionMessage
from asyncmcp.sns_sqs.utils import SnsSqsTransportConfig

# AWS LocalStack configuration
AWS_CONFIG = {
    "region_name": "us-east-1",
    "endpoint_url": "http://localhost:4566",
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}

# Resource ARNs and URLs for LocalStack
RESOURCES = {
    "client_request_topic": "arn:aws:sns:us-east-1:000000000000:mcp-requests",
    "server_response_topic": "arn:aws:sns:us-east-1:000000000000:mcp-response",
    "client_response_queue": "http://localhost:4566/000000000000/mcp-consumer",
    "server_request_queue": "http://localhost:4566/000000000000/mcp-processor",
}

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


def create_client_transport_config(
    client_id: str = "mcp-client", timeout: Optional[float] = None
) -> tuple[SnsSqsTransportConfig, Any, Any]:
    """Create a standard client transport configuration"""
    sqs_client, sns_client = setup_aws_clients()

    config = SnsSqsTransportConfig(
        sqs_queue_url=RESOURCES["client_response_queue"],
        sns_topic_arn=RESOURCES["client_request_topic"],
        max_messages=1,
        wait_time_seconds=5,
        poll_interval_seconds=2.0,
        client_id=client_id,
        transport_timeout_seconds=timeout,
    )

    return config, sqs_client, sns_client


def create_server_transport_config() -> tuple[SnsSqsTransportConfig, Any, Any]:
    """Create a standard server transport configuration"""
    sqs_client, sns_client = setup_aws_clients()

    config = SnsSqsTransportConfig(
        sqs_queue_url=RESOURCES["server_request_queue"],
        sns_topic_arn=RESOURCES["server_response_topic"],
        max_messages=10,
        wait_time_seconds=5,
        poll_interval_seconds=1.0,
    )

    return config, sqs_client, sns_client


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


def calculate_tool(params: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate tool - performs math operations"""
    try:
        operation = params.get("operation", "add")
        a = float(params.get("a", 0))
        b = float(params.get("b", 0))

        if operation == "add":
            result = a + b
        elif operation == "subtract":
            result = a - b
        elif operation == "multiply":
            result = a * b
        elif operation == "divide":
            if b == 0:
                raise ValueError("Division by zero")
            result = a / b
        else:
            raise ValueError(f"Unknown operation: {operation}")

        return {"operation": operation, "a": a, "b": b, "result": result}
    except (ValueError, TypeError) as e:
        raise ValueError(f"Calculation error: {e}")


def get_standard_tools_list() -> List[Dict[str, Any]]:
    """Get the standard list of tools"""
    return [
        {
            "name": "echo",
            "description": "Echo back the provided message",
            "inputSchema": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "The message to echo back"}},
                "required": ["message"],
            },
        },
        {
            "name": "calculate",
            "description": "Perform mathematical calculations",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "The mathematical operation to perform",
                    },
                    "a": {"type": "number", "description": "The first number"},
                    "b": {"type": "number", "description": "The second number"},
                },
                "required": ["operation", "a", "b"],
            },
        },
        {
            "name": "process_data",
            "description": "Process data in background (async simulation)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "data": {"type": "string", "description": "The data to process"},
                    "processing_time": {"type": "integer", "description": "Processing time in seconds", "default": 5},
                },
                "required": ["data"],
            },
        },
    ]


@dataclass
class BackgroundTask:
    """Represents a background processing task"""

    request_id: int
    method: str
    params: Dict[str, Any]
    submitted_at: float
    client_id: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackgroundTask":
        """Create from dictionary"""
        return cls(**data)


class TaskStorage:
    """Persistent task storage using JSON file"""

    def __init__(self, storage_file: str = "background_tasks.json"):
        self.storage_file = storage_file

    def load_tasks(self) -> List[BackgroundTask]:
        """Load tasks from storage"""
        if not os.path.exists(self.storage_file):
            return []

        try:
            with open(self.storage_file, "r") as f:
                data = json.load(f)
                return [BackgroundTask.from_dict(task_data) for task_data in data]
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            return []

    def save_tasks(self, tasks: List[BackgroundTask]):
        """Save tasks to storage"""
        try:
            with open(self.storage_file, "w") as f:
                json.dump([task.to_dict() for task in tasks], f, indent=2)
        except Exception as e:
            logging.error(f"Error saving tasks: {e}")

    def add_task(self, task: BackgroundTask):
        """Add a new task"""
        tasks = self.load_tasks()
        tasks.append(task)
        self.save_tasks(tasks)

    def remove_task(self, task_id: int) -> Optional[BackgroundTask]:
        """Remove and return a task by ID"""
        tasks = self.load_tasks()
        for i, task in enumerate(tasks):
            if task.request_id == task_id:
                removed_task = tasks.pop(i)
                self.save_tasks(tasks)
                return removed_task
        return None

    def get_task(self, task_id: int) -> Optional[BackgroundTask]:
        """Get a task by ID"""
        tasks = self.load_tasks()
        for task in tasks:
            if task.request_id == task_id:
                return task
        return None


def process_data_tool(
    params: Dict[str, Any], request_id: int, client_id: str = "unknown", task_storage: Optional[TaskStorage] = None
) -> Dict[str, Any]:
    """Process data tool - background processing simulation"""
    data = params.get("data", "")
    processing_time = params.get("processing_time", 5)

    # Create background task
    task = BackgroundTask(
        request_id=request_id, method="tools/call", params=params, submitted_at=time.time(), client_id=client_id
    )

    # Store task if storage provided
    if task_storage:
        task_storage.add_task(task)

    return {
        "status": "processing",
        "task_id": request_id,
        "data": data,
        "processing_time": processing_time,
        "submitted_at": task.submitted_at,
        "message": f"Background processing started for: {data}",
    }


def ensure_localstack_running():
    try:
        response = requests.get("http://localhost:4566/health", timeout=2)
        if response.status_code == 200:
            print_colored("🟢 LocalStack is already running.", "green")
            return
    except Exception:
        print_colored("🔴 LocalStack not running. Starting LocalStack...", "yellow")
        subprocess.Popen(["localstack", "start", "-d"])
        # Wait for LocalStack to be ready
        for _ in range(30):
            try:
                response = requests.get("http://localhost:4566/health", timeout=2)
                if response.status_code == 200:
                    print_colored("🟢 LocalStack started successfully.", "green")
                    return
            except Exception:
                time.sleep(1)
        print_colored("❌ Failed to start LocalStack.", "red")
        sys.exit(1)


# Ensure LocalStack is running before setup
ensure_localstack_running()

# Create AWS clients
sqs_client, sns_client = setup_aws_clients()
