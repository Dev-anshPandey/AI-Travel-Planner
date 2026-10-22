import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fakes import REQUEST, SOURCE_URL, FakeLLM
from travel_planner.agents import Agents
from travel_planner.api import create_app
from travel_planner.config import Settings
from travel_planner.tools import WebSearch


@pytest.fixture
def setup(tmp_path):
    llm = FakeLLM()
    searches = []

    def handler(request):
        searches.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Paris guide",
                        "url": SOURCE_URL,
                        "highlights": ["Museum and city tips"],
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        agents = Agents(WebSearch(http, "test-exa-key"), llm)
        settings = Settings(_env_file=None, database_path=str(tmp_path / "state.sqlite"))
        yield settings, agents, llm, searches


@pytest.fixture
def client(setup):
    settings, agents, _, _ = setup
    with TestClient(create_app(settings, agents)) as client:
        yield client


def submit(client):
    response = client.post("/plan", json=REQUEST)
    assert response.status_code == 202
    plan_id = response.json()["id"]
    assert response.headers["location"] == f"/plan/{plan_id}"
    return plan_id
