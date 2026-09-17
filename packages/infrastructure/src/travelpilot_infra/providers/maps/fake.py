import math
from travelpilot_core.ports.maps import Place, Route

PLACES = [
    Place("fake", "fake-panda", "成都大熊猫繁育研究基地", "成都市成华区", "ATTRACTION", 30.7337, 104.1477, {"open": "07:30", "close": "18:00"}, 180),
    Place("fake", "fake-kuanzhai", "宽窄巷子", "成都市青羊区", "ATTRACTION", 30.6633, 104.0647, {"open": "00:00", "close": "23:59"}, 120),
]

def _distance(a, b):
    return int(math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 96000))

class FakeMapsProvider:
    async def search_place(self, keyword, city=None):
        return [place for place in PLACES if keyword in place.name]
    async def get_place_detail(self, provider_id):
        for place in PLACES:
            if place.provider_id == provider_id:
                return place
        raise LookupError("place not found")
    async def get_route(self, origin, destination):
        distance = _distance(origin, destination)
        return Route(origin, destination, distance, max(1, round(distance / 350)), "fake")
    async def get_distance_matrix(self, points):
        return [[_distance(a, b) // 350 for b in points] for a in points]
