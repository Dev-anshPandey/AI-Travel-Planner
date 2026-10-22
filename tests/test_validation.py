import copy
from uuid import uuid4

import pytest

from tests.conftest import REQUEST, submit


@pytest.mark.parametrize(
    "changes",
    [
        {"destination": " "},
        {"travelers": 0},
        {"travelers": 21},
        {"end_date": "2027-05-09"},
        {"end_date": "2027-06-01"},
        {"budget_min": 1001},
        {"budget_max": 0},
        {"interests": []},
        {"interests": [" "]},
        {"currency": "eur"},
        {"unknown": "field"},
    ],
)
def test_invalid_requests(client, changes):
    assert client.post("/plan", json=REQUEST | changes).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"action": "reject", "revision": 1},
        {"action": "reject", "revision": 1, "feedback": " "},
        {"action": "modify", "revision": 1},
        {"action": "modify", "revision": 1, "changes": {}},
        {"action": "approve", "revision": 1, "changes": {"accommodation": "Hotel"}},
        {"action": "approve", "revision": 1, "rerun_research": True},
        {"action": "approve"},
        {"action": "delete", "revision": 1},
    ],
)
def test_invalid_reviews_leave_pause_intact(client, body):
    plan_id = submit(client)
    before = client.get(f"/plan/{plan_id}").json()
    assert client.post(f"/plan/{plan_id}/review", json=body).status_code == 422
    assert client.get(f"/plan/{plan_id}").json() == before


@pytest.mark.parametrize("error", ["date", "budget", "source", "duplicate_slot", "duplicate_date"])
def test_invalid_modifications_do_not_consume_interrupt(client, error):
    plan_id = submit(client)
    before = client.get(f"/plan/{plan_id}").json()
    day = copy.deepcopy(before["draft"]["days"][0])
    if error == "date":
        day["date"] = "2027-05-12"
    elif error == "budget":
        day["lodging_cost"] = 1001
    elif error == "source":
        day["activities"][0]["source_url"] = "https://invented.example/"
    elif error == "duplicate_slot":
        day["activities"].append(day["activities"][0])
    days = [day, day] if error == "duplicate_date" else [day]
    response = client.post(
        f"/plan/{plan_id}/review",
        json={"action": "modify", "revision": 1, "changes": {"days": days}},
    )
    assert response.status_code == 422
    assert client.get(f"/plan/{plan_id}").json() == before
    assert (
        client.post(
            f"/plan/{plan_id}/review", json={"action": "approve", "revision": 1}
        ).status_code
        == 202
    )


def test_unknown_and_malformed_ids(client):
    for plan_id, expected in [(str(uuid4()), 404), ("invalid", 422)]:
        assert client.get(f"/plan/{plan_id}").status_code == expected
        assert client.get(f"/plan/{plan_id}/final").status_code == expected
        assert client.post(f"/plan/{plan_id}/retry").status_code == expected
        assert (
            client.post(
                f"/plan/{plan_id}/review", json={"action": "approve", "revision": 1}
            ).status_code
            == expected
        )


def test_sessions_are_isolated(client):
    first, second = submit(client), submit(client)
    client.post(f"/plan/{first}/review", json={"action": "approve", "revision": 1})
    assert client.get(f"/plan/{second}").json()["status"] == "awaiting_review"
    assert client.get(f"/plan/{second}/final").status_code == 409
