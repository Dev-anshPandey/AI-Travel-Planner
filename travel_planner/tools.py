from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal

import httpx

from travel_planner.models import Source, TravelRequest


class ProviderError(RuntimeError):
    """A safe-to-display error at an external service boundary."""


class WebSearch:
    def __init__(self, client: httpx.Client, api_key: str):
        self.client = client
        self.api_key = api_key

    def search(self, query: str) -> list[dict]:
        try:
            response = self.client.post(
                "https://api.exa.ai/search",
                headers={"x-api-key": self.api_key},
                json={
                    "query": query,
                    "type": "auto",
                    "numResults": 6,
                    "contents": {"highlights": True},
                },
            )
            response.raise_for_status()
            results = response.json()["results"]
            sources = [
                Source(
                    title=r.get("title") or r["url"],
                    url=r["url"],
                    excerpt="\n".join(r.get("highlights") or [])[:6000],
                ).model_dump()
                for r in results
            ]
            if not sources:
                raise ValueError("No results")
            return sources
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError(
                "Web search failed; check Exa credentials, quota and connectivity"
            ) from exc


def travel_context(request: TravelRequest) -> dict:
    """Local travel checklist, not a weather forecast or destination database."""
    return {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "dates": [d.isoformat() for d in request.dates],
        "nights": len(request.dates) - 1,
        "checklist": [
            "Verify entry rules for each traveler's nationality",
            "Check official destination advisories before departure",
            "Recheck opening hours, reservations and accessibility",
            "Seasonal climate is not a forecast; check weather close to departure",
        ],
        "scope": "Group costs in requested currency; international travel excluded",
    }


def allocate_budget(request: TravelRequest) -> dict:
    total = Decimal(str(request.budget_max))
    shares = {"lodging": ".35", "meals": ".25", "activities": ".20", "transport": ".10"}
    amounts = {
        name: (total * Decimal(share)).quantize(Decimal(".01"), rounding=ROUND_DOWN)
        for name, share in shares.items()
    }
    return {
        "currency": request.currency,
        "group_size": request.travelers,
        "category_limits": {k: float(v) for k, v in amounts.items()},
        "reserve": float(total - sum(amounts.values())),
        "daily_group_limit": float((total / len(request.dates)).quantize(Decimal(".01"))),
        "note": "Planning targets, not live prices; keep total below maximum including reserve",
    }


def packing_list(request: TravelRequest) -> list[str]:
    items = [
        "Travel documents and insurance details",
        "Medication and prescriptions",
        "Phone charger and destination-compatible adapter",
        "Comfortable walking shoes",
        "Reusable water bottle",
        "Weather-appropriate layers; check forecast before departure",
    ]
    interests = " ".join(request.interests).lower()
    if any(word in interests for word in ("hike", "hiking", "outdoor", "nature")):
        items += ["Trail footwear and lightweight rain protection"]
    if any(word in interests for word in ("beach", "swim")):
        items += ["Swimwear and sun protection"]
    return items
