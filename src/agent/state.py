"""State bersama yang dibaca & ditulis tiap node."""

from __future__ import annotations

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class State(TypedDict):
    """State percakapan.

    `messages` memakai reducer `add_messages`: pesan baru ditambahkan ke riwayat,
    bukan menimpa. Riwayat ini yang disimpan checkpointer per `thread_id`.
    """

    messages: Annotated[list[AnyMessage], add_messages]
