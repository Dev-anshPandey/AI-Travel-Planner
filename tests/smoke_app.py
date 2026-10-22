"""Test-only Uvicorn app: real workflow and database, explicit fake provider boundaries."""

import httpx

from tests.fakes import SOURCE_URL, FakeLLM
from travel_planner.agents import Agents
from travel_planner.api import create_app
from travel_planner.config import Settings
from travel_planner.tools import WebSearch

http = httpx.Client(
    transport=httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Fixture guide", "url": SOURCE_URL, "highlights": ["Test source"]}
                ]
            },
        )
    )
)
app = create_app(Settings(), Agents(WebSearch(http, "test-key"), FakeLLM()))
