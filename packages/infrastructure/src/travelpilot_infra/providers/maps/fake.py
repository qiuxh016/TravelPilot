import math

from travelpilot_core.ports.maps import Place, Route

PLACES = [
    Place("fake", "fake-panda", "成都大熊猫繁育研究基地", "成都市成华区", "ATTRACTION;景点", 30.7337, 104.1477, {"open": "07:30", "close": "18:00"}, 180),
    Place("fake", "fake-kuanzhai", "宽窄巷子", "成都市青羊区", "ATTRACTION;景点", 30.6633, 104.0647, {"open": "00:00", "close": "23:59"}, 120),
    Place("fake", "fake-restaurant", "模拟川味餐馆", "成都市青羊区", "餐饮服务;中餐厅;川菜", 30.6661, 104.0612, {"open": "10:00", "close": "22:00"}, 75),
    Place("fake", "fake-hotel", "模拟旅行酒店", "成都市锦江区", "住宿服务;酒店", 30.6574, 104.0811, {"open": "00:00", "close": "23:59"}, 0),
]

SIMULATED_INDOOR_PLACE = Place(
    "fake", "fake-indoor-exhibit", "模拟室内展馆", "成都市评测区",
    "室内景点;展馆（模拟）", 30.6701, 104.0702,
    {"open": "09:00", "close": "17:00"}, 90,
)

def _distance(a, b):
    return int(math.hypot((a[0] - b[0]) * 111000, (a[1] - b[1]) * 96000))

class FakeMapsProvider:
    async def search_place(self, keyword, city=None):
        query = keyword.casefold().strip()
        if query == "室内景点":
            return [SIMULATED_INDOOR_PLACE]
        return [
            place for place in PLACES
            if query in f"{place.name} {place.category} {place.address}".casefold()
        ]
    async def get_place_detail(self, provider_id):
        for place in [*PLACES, SIMULATED_INDOOR_PLACE]:
            if place.provider_id == provider_id:
                return place
        raise LookupError("place not found")
    async def get_route(self, origin, destination, mode="DRIVING", *, origin_city_code=None, destination_city_code=None):
        distance = _distance(origin, destination)
        speed = 75 if mode == "WALKING" else 350 if mode == "DRIVING" else 250
        segments = ({"mode": "BUS", "label": "模拟公交换乘路线"},) if mode == "TRANSIT" else ()
        return Route(
            origin, destination, distance, max(1, round(distance / speed)), "fake",
            polyline=(origin, destination), mode=mode, segments=segments,
        )
    async def get_distance_matrix(self, points):
        return [[_distance(a, b) // 350 for b in points] for a in points]
