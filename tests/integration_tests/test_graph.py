import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import graph

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="LLM_API_KEY belum diisi")
async def test_agent_replies_via_real_llm() -> None:
    res = await graph.ainvoke({"messages": [HumanMessage("Say hello in one short sentence.")]})
    assert isinstance(res["messages"][-1], AIMessage)
    assert res["messages"][-1].content
