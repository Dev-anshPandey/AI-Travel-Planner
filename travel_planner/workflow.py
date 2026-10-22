from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from travel_planner.models import Itinerary, Review, TravelRequest, apply_changes


class PlanState(TypedDict, total=False):
    request: dict
    research: dict
    sources: list[dict]
    travel_context: dict
    budget: dict
    draft: dict
    final: dict
    revision: int
    feedback: str
    review: dict
    reviews: list[dict]


def build_graph(agents, checkpointer):
    def validate(state):
        request = TravelRequest.model_validate(state["request"])
        return {"request": request.model_dump(mode="json")}

    def approval(state):
        decision = interrupt(
            {
                "draft": state["draft"],
                "revision": state["revision"],
                "actions": ["approve", "reject", "modify"],
            }
        )
        review = Review.model_validate(decision)
        if review.revision != state["revision"]:
            raise ValueError("Stale review revision")
        return {
            "review": review.model_dump(mode="json"),
            "reviews": state.get("reviews", []) + [review.model_dump(mode="json")],
            "feedback": review.feedback,
        }

    def route_review(state):
        review = state["review"]
        if review["action"] == "approve":
            return "finalize"
        if review["action"] == "modify":
            return "modify"
        return "research" if review["rerun_research"] else "planning"

    def modify(state):
        review = Review.model_validate(state["review"])
        draft = apply_changes(
            state["draft"],
            review.changes,
            TravelRequest.model_validate(state["request"]),
            state["sources"],
        )
        return {"draft": draft, "revision": state["revision"] + 1}

    def finalize(state):
        plan = Itinerary.model_validate(state["draft"])
        plan.check_trip(TravelRequest.model_validate(state["request"]), state["sources"])
        return {"final": plan.model_dump(mode="json")}

    graph = StateGraph(PlanState)
    for name, node in [
        ("validate", validate),
        ("research", agents.research),
        ("planning", agents.plan),
        ("approval", approval),
        ("modify", modify),
        ("finalize", finalize),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "validate")
    graph.add_edge("validate", "research")
    graph.add_edge("research", "planning")
    graph.add_edge("planning", "approval")
    graph.add_conditional_edges(
        "approval", route_review, ["research", "planning", "modify", "finalize"]
    )
    graph.add_edge("modify", "approval")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
