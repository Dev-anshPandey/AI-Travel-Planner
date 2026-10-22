from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.conftest import submit
from travel_planner.api import create_app
from travel_planner.models import Review


def test_approve_after_restart(setup):
    settings, agents, llm, searches = setup
    with TestClient(create_app(settings, agents)) as client:
        plan_id = submit(client)
        before = client.get(f"/plan/{plan_id}").json()
        assert before["status"] == "awaiting_review"
        assert before["stage"] == "approval"
        assert before["revision"] == 1
        assert before["estimated_total"] == 260
        assert len(searches) == 2
        assert len(llm.calls) == 2
        assert client.get(f"/plan/{plan_id}/final").status_code == 409
    with TestClient(create_app(settings, agents)) as client:
        assert client.get(f"/plan/{plan_id}").json() == before
        response = client.post(f"/plan/{plan_id}/review", json={"action": "approve", "revision": 1})
        assert response.status_code == 202
        result = client.get(f"/plan/{plan_id}/final")
        assert result.status_code == 200
        assert result.json()["plan"] == before["draft"]
        assert len(llm.calls) == 2  # Approval does not rerun providers.
        assert client.get(f"/plan/{plan_id}").json()["status"] == "completed"
        assert (
            client.post(
                f"/plan/{plan_id}/review", json={"action": "approve", "revision": 1}
            ).status_code
            == 409
        )


def test_reject_routes_to_planner_then_research(client, setup):
    _, _, llm, searches = setup
    plan_id = submit(client)
    for revision, rerun in [(1, False), (2, True)]:
        response = client.post(
            f"/plan/{plan_id}/review",
            json={
                "action": "reject",
                "revision": revision,
                "feedback": "Prefer outdoor art",
                "rerun_research": rerun,
            },
        )
        assert response.status_code == 202
        view = client.get(f"/plan/{plan_id}").json()
        assert view["status"] == "awaiting_review"
        assert view["revision"] == revision + 1
        assert view["draft"]["days"][0]["activities"][0]["description"] == "Prefer outdoor art"
        assert client.get(f"/plan/{plan_id}/final").status_code == 409
    assert len(searches) == 5
    assert [name for name, _ in llm.calls] == [
        "Research",
        "Itinerary",
        "Itinerary",
        "Research",
        "Itinerary",
    ]
    assert len(view["reviews"]) == 2
    assert llm.calls[-1][1]["previous_draft"] is not None


def test_modify_repauses_and_preserves_other_days(client):
    plan_id = submit(client)
    draft = client.get(f"/plan/{plan_id}").json()["draft"]
    day = dict(draft["days"][0])
    day["activities"] = [dict(day["activities"][0], name="Gallery")]
    response = client.post(
        f"/plan/{plan_id}/review",
        json={
            "action": "modify",
            "revision": 1,
            "changes": {"days": [day], "accommodation": "Quiet hotel"},
        },
    )
    assert response.status_code == 202
    view = client.get(f"/plan/{plan_id}").json()
    assert view["status"] == "awaiting_review"
    assert view["revision"] == 2
    assert view["draft"]["days"][0] == day
    assert view["draft"]["days"][1] == draft["days"][1]
    assert view["draft"]["accommodation"] == "Quiet hotel"
    assert client.get(f"/plan/{plan_id}/final").status_code == 409
    assert (
        client.post(
            f"/plan/{plan_id}/review", json={"action": "approve", "revision": 1}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/plan/{plan_id}/review", json={"action": "approve", "revision": 2}
        ).status_code
        == 202
    )
    assert client.get(f"/plan/{plan_id}/final").json()["plan"] == view["draft"]


def test_failed_node_survives_restart_and_retries(setup):
    settings, agents, llm, _ = setup
    llm.fail = True
    with TestClient(create_app(settings, agents)) as client:
        plan_id = submit(client)
        assert client.get(f"/plan/{plan_id}").json()["status"] == "failed"
        assert client.get(f"/plan/{plan_id}/final").status_code == 409
    llm.fail = False
    with TestClient(create_app(settings, agents)) as client:
        assert client.post(f"/plan/{plan_id}/retry").status_code == 202
        assert client.get(f"/plan/{plan_id}").json()["status"] == "awaiting_review"
        assert client.post(f"/plan/{plan_id}/retry").status_code == 409


def test_rejected_revision_failure_retries_planning(client, setup):
    _, _, llm, _ = setup
    plan_id = submit(client)
    llm.fail = True
    client.post(
        f"/plan/{plan_id}/review",
        json={"action": "reject", "revision": 1, "feedback": "More museums"},
    )
    view = client.get(f"/plan/{plan_id}").json()
    assert view["status"] == "failed"
    assert view["stage"] == "planning"
    assert len(view["reviews"]) == 1
    llm.fail = False
    client.post(f"/plan/{plan_id}/retry")
    view = client.get(f"/plan/{plan_id}").json()
    assert view["revision"] == 2
    assert len(view["reviews"]) == 1


def test_concurrent_reviews_allow_only_one(client):
    plan_id = submit(client)
    service = client.app.state.service

    def prepare(_):
        try:
            return service.prepare_review(plan_id, Review(action="approve", revision=1))
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(prepare, range(2)))
    assert sum(outcome == 409 for outcome in outcomes) == 1
    command = next(outcome for outcome in outcomes if outcome != 409)
    service.run(plan_id, command)
    assert service.view(plan_id)["status"] == "completed"


def test_pending_work_can_resume_after_restart(setup):
    from tests.conftest import REQUEST
    from travel_planner.models import TravelRequest

    settings, agents, _, _ = setup
    with TestClient(create_app(settings, agents)) as client:
        plan_id = client.app.state.service.create(TravelRequest.model_validate(REQUEST))
    with TestClient(create_app(settings, agents)) as client:
        assert client.get(f"/plan/{plan_id}").json()["status"] == "pending"
        assert client.post(f"/plan/{plan_id}/retry").status_code == 202
        assert client.get(f"/plan/{plan_id}").json()["status"] == "awaiting_review"


def test_modified_draft_and_final_survive_restarts(setup):
    settings, agents, _, _ = setup
    with TestClient(create_app(settings, agents)) as client:
        plan_id = submit(client)
        client.post(
            f"/plan/{plan_id}/review",
            json={"action": "modify", "revision": 1, "changes": {"accommodation": "Quiet hotel"}},
        )
        modified = client.get(f"/plan/{plan_id}").json()
    with TestClient(create_app(settings, agents)) as client:
        assert client.get(f"/plan/{plan_id}").json() == modified
        client.post(f"/plan/{plan_id}/review", json={"action": "approve", "revision": 2})
        final = client.get(f"/plan/{plan_id}/final").json()
    with TestClient(create_app(settings, agents)) as client:
        assert client.get(f"/plan/{plan_id}/final").json() == final
        assert final["plan"] == modified["draft"]
        assert len(client.get(f"/plan/{plan_id}").json()["reviews"]) == 2
