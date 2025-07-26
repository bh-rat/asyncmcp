"""
Integration tests for validation flows across all transport types.
"""

import json

import pytest
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, PARSE_ERROR

from asyncmcp.common.utils import (
    create_invalid_params_error_response,
    create_invalid_request_error_response,
    create_parse_error_response,
    create_protocol_version_error_response,
    create_session_id_error_response,
    validate_and_parse_message,
    validate_message_attributes,
    validate_protocol_version,
    validate_session_id,
)


class TestCrossTransportValidation:
    """Test validation consistency across all transport types."""

    @pytest.mark.parametrize("transport_type", ["webhook", "sqs", "sns_sqs"])
    def test_protocol_version_validation_consistency(self, transport_type):
        """Test that protocol version validation is consistent across transports."""
        # Valid versions should pass
        for version in SUPPORTED_PROTOCOL_VERSIONS:
            assert validate_protocol_version(version) is True

        # None should pass (default)
        assert validate_protocol_version(None) is True

        # Invalid versions should fail
        invalid_versions = ["1.0", "3.0", "invalid", ""]
        for version in invalid_versions:
            assert validate_protocol_version(version) is False

    @pytest.mark.parametrize("transport_type", ["webhook", "sqs", "sns_sqs"])
    def test_session_id_validation_consistency(self, transport_type):
        """Test that session ID validation is consistent across transports."""
        # Valid session IDs
        valid_ids = ["valid-session-123", "ABC123_xyz", "!@#$%^&*()", "session.with.dots"]
        for session_id in valid_ids:
            assert validate_session_id(session_id) is True

        # Invalid session IDs
        invalid_ids = ["", "session with spaces", "session\nwith\nnewline", "session\twith\ttab"]
        for session_id in invalid_ids:
            assert validate_session_id(session_id) is False

    def test_error_response_format_consistency(self):
        """Test that error responses have consistent format across all functions."""
        # Test parse error
        parse_error = create_parse_error_response("Test parse error", "req-1")
        assert parse_error.jsonrpc == "2.0"
        assert parse_error.id == "req-1"
        assert parse_error.error.code == PARSE_ERROR
        assert "Test parse error" in parse_error.error.message

        # Test invalid request error
        invalid_request_error = create_invalid_request_error_response("Test invalid request", "req-2")
        assert invalid_request_error.jsonrpc == "2.0"
        assert invalid_request_error.id == "req-2"
        assert invalid_request_error.error.code == INVALID_REQUEST
        assert "Test invalid request" in invalid_request_error.error.message

        # Test invalid params error
        invalid_params_error = create_invalid_params_error_response("Test invalid params", "req-3")
        assert invalid_params_error.jsonrpc == "2.0"
        assert invalid_params_error.id == "req-3"
        assert invalid_params_error.error.code == INVALID_PARAMS
        assert "Test invalid params" in invalid_params_error.error.message

    def test_message_parsing_validation_scenarios(self):
        """Test message parsing validation with various edge cases."""
        # Valid JSON-RPC message
        valid_message = '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}'
        session_msg, error = validate_and_parse_message(valid_message)
        assert session_msg is not None
        assert error is None

        # Invalid JSON
        invalid_json = '{"jsonrpc": "2.0", "id": 1, "method": "test"'  # Missing closing brace
        session_msg, error = validate_and_parse_message(invalid_json)
        assert session_msg is None
        assert error is not None
        assert error.error.code == PARSE_ERROR

        # Invalid JSON-RPC (wrong version)
        invalid_jsonrpc = '{"jsonrpc": "1.0", "id": 1, "method": "test", "params": {}}'
        session_msg, error = validate_and_parse_message(invalid_jsonrpc)
        assert session_msg is None
        assert error is not None
        assert error.error.code == INVALID_PARAMS

        # Empty string
        session_msg, error = validate_and_parse_message("")
        assert session_msg is None
        assert error is not None
        assert error.error.code == PARSE_ERROR

    def test_message_attributes_validation_scenarios(self):
        """Test message attributes validation with various scenarios."""
        # Valid attributes
        valid_attrs = {
            "ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]},
            "SessionId": {"StringValue": "valid-session-123"},
        }
        error = validate_message_attributes(valid_attrs)
        assert error is None

        # Invalid protocol version
        invalid_protocol_attrs = {
            "ProtocolVersion": {"StringValue": "1.5"},
            "SessionId": {"StringValue": "valid-session-123"},
        }
        error = validate_message_attributes(invalid_protocol_attrs)
        assert error is not None
        assert "Unsupported protocol version" in error.error.message

        # Invalid session ID
        invalid_session_attrs = {
            "ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]},
            "SessionId": {"StringValue": "invalid session"},
        }
        error = validate_message_attributes(invalid_session_attrs)
        assert error is not None
        assert "Invalid session ID format" in error.error.message

        # Missing required session ID
        missing_session_attrs = {"ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]}}
        error = validate_message_attributes(missing_session_attrs, require_session_id=True)
        assert error is not None
        assert "Missing or invalid session ID" in error.error.message

        # Session ID mismatch
        mismatch_attrs = {
            "ProtocolVersion": {"StringValue": SUPPORTED_PROTOCOL_VERSIONS[0]},
            "SessionId": {"StringValue": "different-session"},
        }
        error = validate_message_attributes(mismatch_attrs, existing_session_id="existing-session")
        assert error is not None
        assert "Session not found" in error.error.message

    def test_specialized_error_responses(self):
        """Test specialized error response creators."""
        # Protocol version error with version
        proto_error = create_protocol_version_error_response("1.5", "req-1")
        assert "Unsupported protocol version: 1.5" in proto_error.error.message
        assert "Supported versions:" in proto_error.error.message

        # Protocol version error without version
        proto_error_no_version = create_protocol_version_error_response()
        assert "Missing protocol version" in proto_error_no_version.error.message
        assert "Supported versions:" in proto_error_no_version.error.message

        # Session ID error with ID
        session_error = create_session_id_error_response("invalid session", "req-2")
        assert "Invalid session ID format: invalid session" in session_error.error.message

        # Session ID error without ID
        session_error_no_id = create_session_id_error_response()
        assert "Missing or invalid session ID" in session_error_no_id.error.message


class TestValidationErrorPropagation:
    """Test that validation errors propagate correctly through the system."""

    def test_validation_error_codes_are_correct(self):
        """Test that validation errors use correct JSON-RPC error codes."""
        # Parse errors should use -32700
        parse_error = create_parse_error_response("Parse failed")
        assert parse_error.error.code == PARSE_ERROR
        assert parse_error.error.code == -32700

        # Invalid request errors should use -32600
        invalid_request = create_invalid_request_error_response("Invalid request")
        assert invalid_request.error.code == INVALID_REQUEST
        assert invalid_request.error.code == -32600

        # Invalid params errors should use -32602
        invalid_params = create_invalid_params_error_response("Invalid params")
        assert invalid_params.error.code == INVALID_PARAMS
        assert invalid_params.error.code == -32602

    def test_error_message_includes_context(self):
        """Test that error messages include helpful context."""
        # Protocol version error includes supported versions
        proto_error = create_protocol_version_error_response("invalid")
        supported_list = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
        assert supported_list in proto_error.error.message

        # Session ID error includes the invalid ID
        session_error = create_session_id_error_response("bad session id")
        assert "bad session id" in session_error.error.message

    def test_request_id_propagation(self):
        """Test that request IDs are properly propagated in error responses."""
        request_id = "test-request-123"

        # All error types should preserve request ID
        parse_error = create_parse_error_response("Parse error", request_id)
        assert parse_error.id == request_id

        invalid_request = create_invalid_request_error_response("Invalid request", request_id)
        assert invalid_request.id == request_id

        invalid_params = create_invalid_params_error_response("Invalid params", request_id)
        assert invalid_params.id == request_id

        proto_error = create_protocol_version_error_response("1.5", request_id)
        assert proto_error.id == request_id

        session_error = create_session_id_error_response("bad-session", request_id)
        assert session_error.id == request_id


class TestValidationPerformance:
    """Test validation performance characteristics."""

    def test_session_id_validation_performance(self):
        """Test that session ID validation performs well with various inputs."""
        # Test with very long valid session ID
        long_valid_id = "a" * 1000  # 1000 characters of 'a'
        assert validate_session_id(long_valid_id) is True

        # Test with very long invalid session ID
        long_invalid_id = "a" * 500 + " " + "a" * 500  # Space in middle
        assert validate_session_id(long_invalid_id) is False

        # Test with edge characters
        edge_chars = "".join(chr(i) for i in range(0x21, 0x7F))  # All valid ASCII chars
        assert validate_session_id(edge_chars) is True

    def test_protocol_version_validation_performance(self):
        """Test that protocol version validation performs well."""
        # Test with None (should be fast)
        assert validate_protocol_version(None) is True

        # Test with valid versions
        for version in SUPPORTED_PROTOCOL_VERSIONS:
            assert validate_protocol_version(version) is True

        # Test with many invalid versions
        invalid_versions = [f"invalid-{i}" for i in range(100)]
        for version in invalid_versions:
            assert validate_protocol_version(version) is False

    def test_message_parsing_with_large_inputs(self):
        """Test message parsing with large JSON inputs."""
        # Test with large valid message
        large_params = {"data": "x" * 10000}  # Large params object
        large_message = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test", "params": large_params})

        session_msg, error = validate_and_parse_message(large_message)
        assert session_msg is not None
        assert error is None

        # Test with large invalid message
        large_invalid = '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {"data": "' + "x" * 10000
        # Missing closing quotes and braces

        session_msg, error = validate_and_parse_message(large_invalid)
        assert session_msg is None
        assert error is not None
        assert error.error.code == PARSE_ERROR
