import math
from travelpilot_core.ports.maps import Place, Route

PLACES = [
    Place("fake", "fake-panda", "成都大熊猫繁育研究基地", "成都市成华区熊猫大道1375号", "ATTRACTION", 30.7337, 104.1477, {"open":"07:30","close":"18:00"}, 180),
    Place("fake", "fake-kuanzhai", "宽窄巷子", "成都市青羊区长顺上街", "ATTRACTION", 30.6633, 104.0647, {"open":"00:00","close":"23:59"}, 120),
    Place("fake", "fake-wuhou", "武侯祠", "成都市武侯区武侯祠大街231号", "ATTRACTION", 30.6495, 104.0431, {"open":"09:00","close":"18:00"}, 120),
]
def distance(a,b): return int(math.hypot((a[0]-b[0])*111000,(a[1]-b[1])*96000))
class FakeMapsProvider:
    async def search_place(self, keyword, city=None): return [p for p in PLACES if keyword in p.name]
    async def get_place_detail(self, provider_id):
        for p in PLACES:
            if p.provider_id == provider_id: return p
        raise LookupError("place not found")
    async def get_route(self, origin, destination):
        d=distance(origin,destination); return Route(origin,destination,d,max(1,round(d/350)),"fake")
    async def get_distance_matrix(self, points): return [[distance(a,b)//350 for b in points] for a in points]