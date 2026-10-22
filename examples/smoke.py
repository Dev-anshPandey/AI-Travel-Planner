"""Exercise a running API; live mode makes paid Exa and Anthropic requests."""

import argparse
import json
import time
from pathlib import Path

import httpx


def wait_for_review(client, plan_id):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        response = client.get(f"/plan/{plan_id}")
        response.raise_for_status()
        plan = response.json()
        if plan["status"] == "awaiting_review":
            return plan
        if plan["status"] == "failed":
            raise RuntimeError(f"Plan {plan_id} failed: {plan['error']}")
        time.sleep(0.5)
    raise TimeoutError(f"Plan {plan_id} did not reach review in 10 minutes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        response = client.post(
            "/plan", json=json.loads(Path(__file__).with_name("request.json").read_text())
        )
        response.raise_for_status()
        plan_id = response.json()["id"]
        print(f"Created {plan_id}", flush=True)
        plan = wait_for_review(client, plan_id)
        assert client.get(f"/plan/{plan_id}/final").status_code == 409
        response = client.post(
            f"/plan/{plan_id}/review",
            json={
                "action": "reject",
                "revision": plan["revision"],
                "feedback": "Please make the afternoons more relaxed",
                "rerun_research": False,
            },
        )
        response.raise_for_status()
        plan = wait_for_review(client, plan_id)
        response = client.post(
            f"/plan/{plan_id}/review",
            json={
                "action": "modify",
                "revision": plan["revision"],
                "changes": {"accommodation": "Quiet central hotel near public transport"},
            },
        )
        response.raise_for_status()
        plan = wait_for_review(client, plan_id)
        response = client.post(
            f"/plan/{plan_id}/review", json={"action": "approve", "revision": plan["revision"]}
        )
        response.raise_for_status()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get(f"/plan/{plan_id}/final")
            if response.status_code == 200:
                assert response.json()["plan"] == plan["draft"]
                print(f"Passed: reject → modify → approve, revision {plan['revision']}")
                return
            assert response.status_code == 409, response.text
            time.sleep(0.2)
        raise TimeoutError("Finalization did not finish")


if __name__ == "__main__":
    main()
