import copy

from travel_planner.models import Itinerary, Research, TravelRequest
from travel_planner.tools import ProviderError

SOURCE_URL = "https://example.org/paris"
REQUEST = {
    "destination": "Paris",
    "start_date": "2027-05-10",
    "end_date": "2027-05-11",
    "budget_min": 300,
    "budget_max": 1000,
    "currency": "EUR",
    "travelers": 2,
    "interests": ["art", "hiking"],
}


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.invalid_plans = 0

    def generate(self, schema, instruction, context):
        self.calls.append((schema.__name__, copy.deepcopy(context)))
        if self.fail:
            raise ProviderError("Provider unavailable")
        if schema is Research:
            return Research(
                summary="Paris research",
                attractions=["Museum"],
                local_tips=["Book ahead"],
                safety=["Watch valuables"],
                weather_and_season="Mild; check forecast",
                uncertainties=["Prices"],
            )
        request = TravelRequest.model_validate(context["request"])
        days = [
            {
                "date": d.isoformat(),
                "activities": [
                    {
                        "time": "morning",
                        "name": "Museum",
                        "description": context.get("feedback") or "See art",
                        "estimated_cost": 30,
                        "source_url": SOURCE_URL,
                    }
                ],
                "meals_cost": 40,
                "transport_cost": 10,
                "lodging_cost": 100 if i < len(request.dates) - 1 else 0,
            }
            for i, d in enumerate(request.dates)
        ]
        if self.invalid_plans:
            self.invalid_plans -= 1
            days[0]["lodging_cost"] = 5000
        return Itinerary(
            title="Paris trip",
            accommodation="Central hotel",
            days=days,
            packing_list=context["packing"],
            notes=["Group estimates; no flights"],
        )
