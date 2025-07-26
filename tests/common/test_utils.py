import json

import pytest
from mcp import types
from mcp.shared.message import SessionMessage
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, INVALID_REQUEST, PARSE_ERROR

from asyncmcp.common.utils import (
    create_internal_error_response,
    create_invalid_params_error_response,
    create_invalid_request_error_response,
    create_jsonrpc_error_response,
    create_parse_error_response,
    create_protocol_version_error_response,
    create_session_id_error_response,
    create_session_not_found_error_response,
    create_session_terminated_error_response,
    is_initialize_request,
    to_session_message,
    validate_and_parse_message,
    validate_message_attributes,
    validate_protocol_version,
    validate_session_id,
)


class TestValidationFunctions:
    """Test validation utility functions."""

    def test_validate_protocol_version_none(self):
        """Test that None protocol version is valid (default)."""
        assert validate_protocol_version(None) is True

    def test_validate_protocol_version_valid(self):
        """Test that supported protocol versions are valid."""
        for version in SUPPORTED_PROTOCOL_VERSIONS:
            assert validate_protocol_version(version) is True

    def test_validate_protocol_version_invalid(self):
        """Test that unsupported protocol versions are invalid."""
        invalid_versions = ["1.0", "3.0", "invalid", ""]
        for version in invalid_versions:
            assert validate_protocol_version(version) is False

    @pytest.mark.parametrize(
        "session_id,expected",
        [
            ("valid-session-123", True),
            ("ABC123_xyz", True),
            ("!@#$%^&*()", True),  # All visible ASCII chars
            ("session-with-dashes", True),
            ("session.with.dots", True),
            ("", False),  # Empty string
            ("session with spaces", False),  # Contains space
            ("session\nwith\nnewline", False),  # Contains newline
            ("session\twith\ttab", False),  # Contains tab
            ("session\x00with\x00null", False),  # Contains null
            ("session\x7fwith\x7fdel", False),  # Contains DEL character
            ("session\x20with\x20space", False),  # Contains space (0x20)
        ],
    )
    def test_validate_session_id(self, session_id, expected):
        """Test session ID validation with various inputs."""
        assert validate_session_id(session_id) is expected

    def test_is_initialize_request_true(self):
        """Test detection of initialize request."""
        request = types.JSONRPCRequest(jsonrpc="2.0", id="1", method="initialize", params={"capabilities": {}})
        session_message = SessionMessage(types.JSONRPCMessage(request))
        assert is_initialize_request(session_message) is True

    def test_is_initialize_request_false_different_method(self):
        """Test that non-initialize requests are not detected."""
        request = types.JSONRPCRequest(jsonrpc="2.0", id="1", method="tools/list", params={})
        session_message = SessionMessage(types.JSONRPCMessage(request))
        assert is_initialize_request(session_message) is False

    def test_is_initialize_request_false_notification(self):
        """Test that notifications are not initialize requests."""
        notification = types.JSONRPCNotification(jsonrpc="2.0", method="initialize", params={"capabilities": {}})
        session_message = SessionMessage(types.JSONRPCMessage(notification))
        assert is_initialize_request(session_message) is False


class TestErrorResponseCreators:
    """Test error response creation functions."""

    def test_create_jsonrpc_error_response_defaults(self):
        """Test creating error response with defaults."""
        error = create_jsonrpc_error_response("Test error")
        assert error.jsonrpc == "2.0"
        assert error.id == "server-error"
        assert error.error.code == INVALID_REQUEST
        assert error.error.message == "Test error"

    def test_create_jsonrpc_error_response_custom(self):
        """Test creating error response with custom values."""
        error = create_jsonrpc_error_response("Custom error", PARSE_ERROR, "req-123")
        assert error.jsonrpc == "2.0"
        assert error.id == "req-123"
        assert error.error.code == PARSE_ERROR
        assert error.error.message == "Custom error"

    def test_create_parse_error_response(self):
        """Test creating parse error response."""
        error = create_parse_error_response("JSON parse failed", "req-456")
        assert error.error.code == PARSE_ERROR
        assert error.error.message == "JSON parse failed"
        assert error.id == "req-456"

    def test_create_invalid_request_error_response(self):
        """Test creating invalid request error response."""
        error = create_invalid_request_error_response("Bad request", "req-789")
        assert error.error.code == INVALID_REQUEST
        assert error.error.message == "Bad request"
        assert error.id == "req-789"

    def test_create_invalid_params_error_response(self):
        """Test creating invalid params error response."""
        error = create_invalid_params_error_response("Bad params", "req-101")
        assert error.error.code == INVALID_PARAMS
        assert error.error.message == "Bad params"
        assert error.id == "req-101"

    def test_create_internal_error_response(self):
        """Test creating internal error response."""
        error = create_internal_error_response("Server crashed", "req-112")
        assert error.error.code == INTERNAL_ERROR
        assert error.error.message == "Server crashed"
        assert error.id == "req-112"

    def test_create_session_not_found_error_response(self):
        """Test creating session not found error response."""
        error = create_session_not_found_error_response("session-123", "req-131")
        assert error.error.code == INVALID_REQUEST
        assert "Session not found: session-123" in error.error.message
        assert error.id == "req-131"

    def test_create_session_not_found_error_response_no_session(self):
        """Test creating session not found error response without session ID."""
        error = create_session_not_found_error_response()
        assert error.error.code == INVALID_REQUEST
        assert error.error.message == "Session not found"

    def test_create_session_terminated_error_response(self):
        """Test creating session terminated error response."""
        error = create_session_terminated_error_response("session-456", "req-141")
        assert error.error.code == INVALID_REQUEST
        assert "Session has been terminated: session-456" in error.error.message
        assert error.id == "req-141"

    def test_create_protocol_version_error_response_with_version(self):
        """Test creating protocol version error response with invalid version."""
        error = create_protocol_version_error_response("1.5", "req-151")
        assert error.error.code == INVALID_REQUEST
        assert "Unsupported protocol version: 1.5" in error.error.message
        assert "Supported versions:" in error.error.message
        assert error.id == "req-151"

    def test_create_protocol_version_error_response_no_version(self):
        """Test creating protocol version error response without version."""
        error = create_protocol_version_error_response()
        assert error.error.code == INVALID_REQUEST
        assert "Missing protocol version" in error.error.message
        assert "Supported versions:" in error.error.message

    def test_create_session_id_error_response_with_id(self):
        """Test creating session ID error response with invalid ID."""
        error = create_session_id_error_response("invalid session", "req-161")
        assert error.error.code == INVALID_REQUEST
        assert "Invalid session ID format: invalid session" in error.error.message
        assert error.id == "req-161"

    def test_create_session_id_error_response_no_id(self):
        """Test creating session ID error response without ID."""
        error = create_session_id_error_response()
        assert error.error.code == INVALID_REQUEST
        assert error.error.message == "Missing or invalid session ID"


class TestMessageProcessing:
    """Test message parsing and validation functions."""

    def test_validate_and_parse_message_valid_json(self):
        """Test parsing valid JSON-RPC message."""
        message_data = {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {"capabilities": {}}}
        message_body = json.dumps(message_data)

        session_message, error = validate_and_parse_message(message_body)
        assert session_message is not None
        assert error is None
        assert isinstance(session_message.message.root, types.JSONRPCRequest)

    def test_validate_and_parse_message_invalid_json(self):
        """Test parsing invalid JSON."""
        message_body = '{"jsonrpc": "2.0", "id": 1, "method": "test"'  # Missing closing brace

        session_message, error = validate_and_parse_message(message_body)
        assert session_message is None
        assert error is not None
        assert error.error.code == PARSE_ERROR
        assert "Parse error:" in error.error.message

    def test_validate_and_parse_message_validation_error(self):
        """Test parsing JSON with validation errors."""
        message_body = '{"jsonrpc": "1.0", "id": 1, "method": "test"}'  # Invalid jsonrpc version

        session_message, error = validate_and_parse_message(message_body)
        assert session_message is None
        assert error is not None
        assert error.error.code == INVALID_PARAMS
        assert "Validation error:" in error.error.message

    def test_validate_message_attributes_valid(self):
        """Test validating valid message attributes."""
        attrs = {
            "ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]},
            "SessionId": {"StringValue": "valid-session-123"},
        }

        error = validate_message_attributes(attrs)
        assert error is None

    def test_validate_message_attributes_invalid_protocol(self):
        """Test validating invalid protocol version."""
        attrs = {"ProtocolVersion": {"StringValue": "1.5"}, "SessionId": {"StringValue": "valid-session-123"}}

        error = validate_message_attributes(attrs)
        assert error is not None
        assert "Unsupported protocol version" in error.error.message

    def test_validate_message_attributes_invalid_session_id(self):
        """Test validating invalid session ID."""
        attrs = {
            "ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]},
            "SessionId": {"StringValue": "invalid session"},
        }

        error = validate_message_attributes(attrs)
        assert error is not None
        assert "Invalid session ID format" in error.error.message

    def test_validate_message_attributes_require_session_id_missing(self):
        """Test requiring session ID when missing."""
        attrs = {"ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]}}

        error = validate_message_attributes(attrs, require_session_id=True)
        assert error is not None
        assert "Missing or invalid session ID" in error.error.message

    def test_validate_message_attributes_session_id_mismatch(self):
        """Test session ID mismatch with existing session."""
        attrs = {
            "ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]},
            "SessionId": {"StringValue": "different-session"},
        }

        error = validate_message_attributes(attrs, existing_session_id="existing-session")
        assert error is not None
        assert "Session not found" in error.error.message

    @pytest.mark.asyncio
    async def test_to_session_message_direct_json(self):
        """Test converting SQS message with direct JSON body."""
        message_data = {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {"capabilities": {}}}
        sqs_message = {"Body": json.dumps(message_data)}

        session_message = await to_session_message(sqs_message)
        assert isinstance(session_message.message.root, types.JSONRPCRequest)
        assert session_message.message.root.method == "initialize"

    @pytest.mark.asyncio
    async def test_to_session_message_sns_wrapped(self):
        """Test converting SQS message with SNS notification wrapper."""
        message_data = {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {"capabilities": {}}}
        sns_notification = {
            "Type": "Notification",
            "Message": json.dumps(message_data),
            "TopicArn": "arn:aws:sns:us-east-1:123456789012:test",
        }
        sqs_message = {"Body": json.dumps(sns_notification)}

        session_message = await to_session_message(sqs_message)
        assert isinstance(session_message.message.root, types.JSONRPCRequest)
        assert session_message.message.root.method == "initialize"

    @pytest.mark.asyncio
    async def test_to_session_message_dict_body(self):
        """Test converting SQS message with dict body."""
        message_data = {"jsonrpc": "2.0", "id": "1", "method": "initialize", "params": {"capabilities": {}}}
        sqs_message = {"Body": message_data}

        session_message = await to_session_message(sqs_message)
        assert isinstance(session_message.message.root, types.JSONRPCRequest)
        assert session_message.message.root.method == "initialize"

    @pytest.mark.asyncio
    async def test_to_session_message_invalid_json(self):
        """Test converting SQS message with invalid JSON."""
        sqs_message = {"Body": '{"invalid": json}'}

        with pytest.raises(ValueError) as exc_info:
            await to_session_message(sqs_message)
        assert "Invalid JSON-RPC message" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_to_session_message_validation_error(self):
        """Test converting SQS message with validation error."""
        sqs_message = {"Body": '{"jsonrpc": "1.0", "id": 1, "method": "test"}'}

        with pytest.raises(ValueError) as exc_info:
            await to_session_message(sqs_message)
        assert "Invalid JSON-RPC message" in str(exc_info.value)
