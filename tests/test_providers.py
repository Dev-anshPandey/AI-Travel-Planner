import json
from decimal import Decimal

import anthropic
import httpx
import pytest

from tests.conftest import REQUEST, submit
from travel_planner.agents import LanguageModel
from travel_planner.config import Settings
from travel_planner.models import Research, TravelRequest
from travel_planner.tools import ProviderError, WebSearch, allocate_budget, packing_list


def test_search_contract(setup):
    _, agents, _, requests = setup
    sources = agents.search.search("Paris museums")
    request = requests[0]
    assert request.url == "https://api.exa.ai/search"
    assert request.headers["x-api-key"] == "test-exa-key"
    assert json.loads(request.content) == {
        "query": "Paris museums",
        "type": "auto",
        "numResults": 6,
        "contents": {"highlights": True},
    }
    assert sources[0]["excerpt"] == "Museum and city tips"


@pytest.mark.parametrize(
    "status, body",
    [
        (401, {}),
        (429, {}),
        (500, {}),
        (200, {}),
        (200, {"results": []}),
        (200, {"results": [None]}),
    ],
)
def test_search_errors_are_sanitized(status, body):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))
    ) as http:
        with pytest.raises(ProviderError, match="Web search failed") as exc:
            WebSearch(http, "secret-key").search("query")
        assert "secret-key" not in str(exc.value)


def test_search_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("secret request", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
        with pytest.raises(ProviderError):
            WebSearch(http, "key").search("query")


def test_anthropic_structured_output_contract():
    research = {
        "summary": "Paris",
        "attractions": ["Museum"],
        "local_tips": ["Book ahead"],
        "safety": ["Watch valuables"],
        "weather_and_season": "Mild",
        "uncertainties": ["Check prices"],
    }

    def handler(request):
        body = json.loads(request.content)
        assert body["tool_choice"] == {"type": "tool", "name": "submit_result"}
        assert body["tools"][0]["input_schema"] == Research.model_json_schema()
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "test-model",
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 20, "output_tokens": 50},
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_test",
                        "name": "submit_result",
                        "input": research,
                    }
                ],
            },
        )

    with anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        result = LanguageModel(client, "test-model").generate(Research, "Research", {})
        assert result.model_dump() == research


def test_planner_repairs_invalid_budget(client, setup):
    _, _, llm, _ = setup
    llm.invalid_plans = 1
    plan_id = submit(client)
    assert client.get(f"/plan/{plan_id}").json()["status"] == "awaiting_review"
    assert "exceeds" in llm.calls[-1][1]["validation_feedback"]


def test_planner_stops_after_bounded_repair(client, setup):
    _, _, llm, _ = setup
    llm.invalid_plans = 2
    plan_id = submit(client)
    assert client.get(f"/plan/{plan_id}").json()["status"] == "failed"
    assert len(llm.calls) == 3


def test_budget_and_packing_tools():
    request = TravelRequest.model_validate(REQUEST | {"budget_max": 1000.01})
    budget = allocate_budget(request)
    assert sum(Decimal(str(v)) for v in budget["category_limits"].values()) + Decimal(
        str(budget["reserve"])
    ) == Decimal("1000.01")
    assert budget["group_size"] == 2
    assert any("Trail" in item for item in packing_list(request))


def test_configuration_fails_clearly_without_keys():
    settings = Settings(_env_file=None, anthropic_api_key="", exa_api_key="")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY and EXA_API_KEY"):
        settings.require_keys()
