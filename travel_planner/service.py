import logging
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException
from langgraph.graph import START
from langgraph.types import Command

from travel_planner.models import Itinerary, Review, TravelRequest, apply_changes

logger = logging.getLogger(__name__)


class PlanService:
    def __init__(self, graph):
        self.graph = graph
        self.lock = RLock()
        self.running: set[str] = set()

    @staticmethod
    def config(plan_id):
        return {"configurable": {"thread_id": plan_id}, "recursion_limit": 30}

    def snapshot(self, plan_id):
        snapshot = self.graph.get_state(self.config(plan_id))
        if not snapshot.values:
            raise HTTPException(404, "Plan not found")
        return snapshot

    def create(self, request):
        plan_id = str(uuid4())
        with self.lock:
            self.graph.update_state(
                self.config(plan_id),
                {"request": request.model_dump(mode="json"), "revision": 0, "reviews": []},
                as_node=START,
            )
            self.running.add(plan_id)
        return plan_id

    def view(self, plan_id):
        with self.lock:
            snapshot = self.snapshot(plan_id)
            state = snapshot.values
            failed = any(task.error for task in snapshot.tasks)
            paused = any(task.interrupts for task in snapshot.tasks)
            active = plan_id in self.running
            status = (
                "completed"
                if state.get("final")
                else "running"
                if active
                else "failed"
                if failed
                else "awaiting_review"
                if paused
                else "pending"
            )
            draft = state.get("draft")
            return {
                "id": plan_id,
                "status": status,
                "stage": "completed"
                if state.get("final")
                else snapshot.next[0]
                if snapshot.next
                else "pending",
                "revision": state.get("revision", 0),
                "request": state["request"],
                "draft": draft,
                "research": state.get("research"),
                "sources": state.get("sources", []),
                "travel_context": state.get("travel_context"),
                "budget": state.get("budget"),
                "reviews": state.get("reviews", []),
                "estimated_total": Itinerary.model_validate(draft).total if draft else None,
                "error": "Workflow failed; check provider configuration and retry"
                if failed and not active
                else None,
            }

    def prepare_review(self, plan_id: str, review: Review):
        with self.lock:
            view = self.view(plan_id)
            if view["status"] != "awaiting_review":
                raise HTTPException(409, "Plan is not awaiting review")
            if review.revision != view["revision"]:
                raise HTTPException(409, "Stale revision; fetch the current draft")
            if review.changes:
                try:
                    apply_changes(
                        view["draft"],
                        review.changes,
                        TravelRequest.model_validate(view["request"]),
                        view["sources"],
                    )
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            self.running.add(plan_id)
            return Command(resume=review.model_dump(mode="json"))

    def prepare_retry(self, plan_id):
        with self.lock:
            if self.view(plan_id)["status"] not in {"failed", "pending"}:
                raise HTTPException(
                    409, "Only failed or interrupted-in-flight plans can be retried"
                )
            self.running.add(plan_id)

    def run(self, plan_id, command=None):
        try:
            self.graph.invoke(command, self.config(plan_id))
        except Exception as exc:
            # Provider bodies and prompts may contain secrets or personal information.
            logger.error("Workflow failed plan_id=%s error_type=%s", plan_id, type(exc).__name__)
        finally:
            with self.lock:
                self.running.discard(plan_id)

    def final(self, plan_id):
        with self.lock:
            state = self.snapshot(plan_id).values
            if not state.get("final"):
                raise HTTPException(409, "Plan has not been approved and finalized")
            return {
                "id": plan_id,
                "revision": state["revision"],
                "plan": state["final"],
                "estimated_total": Itinerary.model_validate(state["final"]).total,
                "currency": state["request"]["currency"],
                "sources": state["sources"],
            }
