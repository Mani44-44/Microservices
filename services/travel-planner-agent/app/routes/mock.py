"""
routes/mock.py
~~~~~~~~~~~~~~
Mock external travel APIs (weather/places/events/restaurants), exposed as
real HTTP routes so other services can hit them directly, even though the
itinerary builder calls the underlying functions in-process for speed.
"""
from fastapi import APIRouter

from app import mock_apis
from app.schemas import EventOut, PlaceOut, RestaurantOut, WeatherOut

router = APIRouter(prefix="/mock", tags=["mock-external-apis"])


@router.get("/weather", response_model=WeatherOut)
async def weather(city: str, date: str):
    return mock_apis.get_weather(city, date)


@router.get("/places", response_model=list[PlaceOut])
async def places(city: str, type: str = "attraction", limit: int = 3):
    return mock_apis.get_places(city, type, limit)


@router.get("/events", response_model=list[EventOut])
async def events(city: str, date: str):
    return mock_apis.get_events(city, date)


@router.get("/restaurants", response_model=list[RestaurantOut])
async def restaurants(city: str, cuisine: str = "Local", price: str = "$$"):
    return mock_apis.get_restaurants(city, cuisine, price)
