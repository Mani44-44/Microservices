from pydantic import BaseModel


class PlanRequest(BaseModel):
    conversation_id: str
    user_id: str
    message: str


class PlanResponse(BaseModel):
    response_text: str
    status: str  # GATHERING | COMPLETE
    constraints: dict
    missing_fields: list[str] | None = None
    itinerary_id: str | None = None
    itinerary: dict | None = None


class WeatherOut(BaseModel):
    temperature: int
    condition: str
    precipitation_chance: int


class PlaceOut(BaseModel):
    name: str
    rating: float
    price_level: int
    description: str


class EventOut(BaseModel):
    name: str
    time: str
    description: str


class RestaurantOut(BaseModel):
    name: str
    cuisine: str
    price: str
    rating: float
