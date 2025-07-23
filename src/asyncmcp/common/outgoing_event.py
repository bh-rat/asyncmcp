from dataclasses import dataclass

from mcp.shared.message import SessionMessage


@dataclass
class OutgoingMessageEvent:
    """Event representing an outgoing message to be sent to a specific session."""
    session_id: str
    message: SessionMessage