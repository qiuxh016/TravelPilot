from fastapi import APIRouter, Depends, HTTPException, Query, Response
from travelpilot_infra.providers.maps.errors import MapsProviderError

from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.schemas.maps import *

router=APIRouter(tags=["maps"])
def unavailable(exc): raise HTTPException(503, detail="地图服务暂时不可用") from exc
@router.get("/places/search",response_model=list[PlaceRead])
async def search(keyword:str=Query(min_length=1),city:str|None=None,maps=Depends(get_maps_provider)):
    try:return await maps.search_place(keyword,city)
    except MapsProviderError as e: unavailable(e)
@router.post("/maps/static-map")
async def static_map(payload:StaticMapRequest,maps=Depends(get_maps_provider)):
    if not hasattr(getattr(maps, "provider", maps), "get_static_map"):
        raise HTTPException(503, detail="真实地图需要将 MAPS_PROVIDER 配置为 amap")
    try:
        content, content_type = await maps.get_static_map(payload.points, payload.selected_index, payload.route_path)
        return Response(content=content, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})
    except MapsProviderError as e: unavailable(e)
@router.get("/places/{provider_id}",response_model=PlaceRead)
async def detail(provider_id:str,maps=Depends(get_maps_provider)):
    try:return await maps.get_place_detail(provider_id)
    except LookupError as e: raise HTTPException(404,"地点不存在") from e
    except MapsProviderError as e: unavailable(e)
@router.post("/routes/estimate",response_model=RouteRead)
async def estimate(p:RouteRequest,maps=Depends(get_maps_provider)):
    try:return await maps.get_route(p.origin,p.destination)
    except MapsProviderError as e: unavailable(e)
@router.post("/routes/matrix",response_model=MatrixRead)
async def matrix(p:MatrixRequest,maps=Depends(get_maps_provider)):
    try:return MatrixRead(durations_min=await maps.get_distance_matrix(p.points),provider=maps.provider.__class__.__name__ if hasattr(maps, "provider") else maps.__class__.__name__)
    except MapsProviderError as e: unavailable(e)
