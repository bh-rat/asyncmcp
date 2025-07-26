from dataclasses import dataclass
from typing import Optional


@dataclass
class ClientState:
    session_id: Optional[str] = None
    client_id: str = ""

    async def set_session_id_if_none(self, new_session_id: str) -> None:
        if self.session_id is None:
            self.session_id = new_session_id
