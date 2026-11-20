import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware import (
    FEEDBACK_ID_PREFIX,
    check_answer,
    current_run,
    extract_prices,
    extract_urls,
    price_supported,
)


def tool_msg(name: str, payload: dict) -> ToolMessage:
    return ToolMessage(json.dumps(payload), tool_call_id="c1", name=name)


WASALT = tool_msg("search_wasalt_listings", {
    "source": "Wasalt", "status": "ok",
    "listings": [{"price_sar": 1800000, "url": "https://wasalt.sa/en/property/sale/villa-1"}],
})
DARGLOBAL = tool_msg("search_darglobal", {
    "source": "DarGlobal", "status": "ok",
    "results": [{"url": "https://darglobal.co.uk/trump-tower-jeddah", "text": "Units from AED 2.5 million"}],
})


@pytest.mark.parametrize(("text", "value"), [
    ("SAR 1,800,000", 1_800_000), ("1.8 million SAR", 1_800_000), ("40k AED", 40_000),
    ("$350", 350), ("60,000 riyals", 60_000), ("SAR 5,500 per m²", 5_500),
])
def test_extract_prices(text: str, value: float) -> None:
    prices = extract_prices(text)
    assert len(prices) == 1 and prices[0].value == value


def test_extract_prices_ignores_non_prices() -> None:
    assert extract_prices("3 bedrooms, 250 m², completed in 2026") == []


def test_price_supported_allows_rounding_only() -> None:
    known = {1_834_000.0}
    assert price_supported(extract_prices("1.8 million SAR")[0], known)
    assert not price_supported(extract_prices("2.5 million SAR")[0], known)


def test_extract_urls_strips_punctuation() -> None:
    assert extract_urls("See https://Wasalt.sa/a/ and (https://x.com/b).") == {"https://wasalt.sa/a", "https://x.com/b"}


def test_grounded_answer_passes() -> None:
    msgs = [HumanMessage("villa Jeddah"), WASALT]
    result = check_answer("Wasalt lists a villa for SAR 1,800,000: https://wasalt.sa/en/property/sale/villa-1", msgs)
    assert result.ok


def test_invented_link_and_price_fail() -> None:
    msgs = [HumanMessage("villa Jeddah"), WASALT]
    result = check_answer("Wasalt: SAR 999,000 at https://wasalt.sa/en/property/sale/villa-9", msgs)
    assert not result.ok
    assert result.unknown_urls and result.unknown_prices and not result.missing_sources
    assert "NOT shown" in result.feedback()


def test_missing_source_fails() -> None:
    msgs = [HumanMessage("Trump Tower Jeddah?"), DARGLOBAL]
    result = check_answer("Units start from AED 2.5 million.", msgs)
    assert result.missing_sources == ["DarGlobal"]


def test_contact_link_and_user_numbers_are_allowed() -> None:
    msgs = [HumanMessage("anything under 80,000 SAR?")]
    result = check_answer("I couldn't find listings under 80,000 SAR. Contact https://darglobal.co.uk/get-in-touch", msgs)
    assert result.ok


def test_not_found_tool_needs_no_source() -> None:
    msgs = [HumanMessage("q"), tool_msg("search_darglobal", {"source": "DarGlobal", "status": "not_found"})]
    assert check_answer("I don't have that information.", msgs).ok


def test_current_run_skips_feedback_messages() -> None:
    msgs = [HumanMessage("old"), AIMessage("a"), HumanMessage("new"), AIMessage("draft"),
            HumanMessage("fix it", id=f"{FEEDBACK_ID_PREFIX}1")]
    assert current_run(msgs)[0].content == "new"
