from pydantic import BaseModel, Field


class PlaceRead(BaseModel):
    provider:str; provider_id:str; name:str; address:str|None; category:str
    latitude:float; longitude:float; opening_hours:dict|None=None
    suggested_duration_min:int=60; raw_metadata:dict|None=None
class RouteRequest(BaseModel):
    origin:tuple[float,float]; destination:tuple[float,float]
class RouteRead(BaseModel):
    distance_m:int; duration_min:int; provider:str; polyline:list[tuple[float,float]]=Field(default_factory=list)
class MatrixRequest(BaseModel):
    points:list[tuple[float,float]]=Field(min_length=2,max_length=30)
class MatrixRead(BaseModel):
    durations_min:list[list[int]]; provider:str
class StaticMapRequest(BaseModel):
    points:list[tuple[float,float]]=Field(min_length=1,max_length=10)
    selected_index:int=Field(default=0,ge=0,le=9)
    route_path:list[tuple[float,float]]|None=Field(default=None,max_length=5000)
