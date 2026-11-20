import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import graph

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY belum diisi")
async def test_agent_replies_via_openrouter() -> None:
    res = await graph.ainvoke({"messages": [HumanMessage("Say hello in one short sentence.")]})
    assert isinstance(res["messages"][-1], AIMessage)
    assert res["messages"][-1].content
