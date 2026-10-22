from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import anthropic
import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from langgraph.checkpoint.sqlite import SqliteSaver

from travel_planner.agents import Agents, LanguageModel
from travel_planner.config import Settings
from travel_planner.models import Review, TravelRequest
from travel_planner.service import PlanService
from travel_planner.tools import WebSearch
from travel_planner.workflow import build_graph


def create_app(settings: Settings | None = None, agents=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings()
        if agents is None:
            config.require_keys()
        Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)
        with (
            SqliteSaver.from_conn_string(config.database_path) as saver,
            httpx.Client(timeout=config.api_timeout_seconds) as http,
            anthropic.Anthropic(
                api_key=config.anthropic_api_key.get_secret_value() or "test",
                timeout=config.api_timeout_seconds,
                max_retries=1,
            ) as client,
        ):
            configured_agents = agents or Agents(
                WebSearch(http, config.exa_api_key.get_secret_value()),
                LanguageModel(client, config.llm_model),
            )
            app.state.service = PlanService(build_graph(configured_agents, saver))
            yield

    app = FastAPI(title="AI Travel Planner", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/plan", status_code=202)
    def create_plan(
        body: TravelRequest, request: Request, tasks: BackgroundTasks, response: Response
    ):
        service = request.app.state.service
        plan_id = service.create(body)
        tasks.add_task(service.run, plan_id)
        response.headers["Location"] = f"/plan/{plan_id}"
        return {"id": plan_id, "status": "pending", "status_url": f"/plan/{plan_id}"}

    @app.get("/plan/{plan_id}")
    def get_plan(plan_id: UUID, request: Request):
        return request.app.state.service.view(str(plan_id))

    @app.post("/plan/{plan_id}/review", status_code=202)
    def review_plan(plan_id: UUID, body: Review, request: Request, tasks: BackgroundTasks):
        service = request.app.state.service
        command = service.prepare_review(str(plan_id), body)
        tasks.add_task(service.run, str(plan_id), command)
        return {"id": str(plan_id), "status": "running", "status_url": f"/plan/{plan_id}"}

    @app.post("/plan/{plan_id}/retry", status_code=202)
    def retry_plan(plan_id: UUID, request: Request, tasks: BackgroundTasks):
        service = request.app.state.service
        service.prepare_retry(str(plan_id))
        tasks.add_task(service.run, str(plan_id))
        return {"id": str(plan_id), "status": "running", "status_url": f"/plan/{plan_id}"}

    @app.get("/plan/{plan_id}/final")
    def final_plan(plan_id: UUID, request: Request):
        return request.app.state.service.final(str(plan_id))

    return app


app = create_app()
