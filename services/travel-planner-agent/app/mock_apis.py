"""
Mock external travel APIs (weather/places/events/restaurants).

Deterministic: seeded on the input params via a hash, so the same
(city, date, ...) always returns the same fake data. No real network calls,
no real LLM — plain templated fakes, per the "no external API keys" scope.
"""
import random

CONDITIONS = ["Sunny", "Cloudy", "Rainy", "Snowy"]

PLACE_ADJECTIVES = ["Grand", "Royal", "Golden", "Hidden", "Old Town", "Riverside", "Sunset"]
PLACE_NOUNS = {
    "attraction": ["Museum", "Gallery", "Tower", "Gardens", "Market", "Cathedral"],
    "activity": ["Museum", "Gallery", "Tower", "Gardens", "Market", "Cathedral"],
    "restaurant": ["Bistro", "Kitchen", "Grill", "Cafe", "Tavern"],
}
DEFAULT_NOUNS = ["Museum", "Gallery", "Tower", "Gardens", "Market", "Cathedral"]

EVENT_TEMPLATES = [
    "{city} Live Music Night",
    "{city} Street Food Festival",
    "{city} Art Walk",
    "{city} Night Market",
    "{city} Cultural Fair",
]

CUISINES = ["Local", "Italian", "Japanese", "French", "Fusion", "Vegetarian"]


def _seeded_rng(*parts: object) -> random.Random:
    key = "|".join(str(p) for p in parts)
    return random.Random(key)


def get_weather(city: str, date: str) -> dict:
    rng = _seeded_rng("weather", city, date)
    return {
        "temperature": rng.randint(5, 35),
        "condition": rng.choice(CONDITIONS),
        "precipitation_chance": rng.randint(0, 100),
    }


def get_places(city: str, type: str = "attraction", limit: int = 3) -> list[dict]:
    rng = _seeded_rng("places", city, type, limit)
    nouns = PLACE_NOUNS.get(type, DEFAULT_NOUNS)
    places = []
    for i in range(limit):
        name = f"{rng.choice(PLACE_ADJECTIVES)} {city} {rng.choice(nouns)}"
        places.append(
            {
                "name": name,
                "rating": round(rng.uniform(3.5, 5.0), 1),
                "price_level": rng.randint(1, 4),
                "description": f"A popular {type} spot in {city}, well reviewed by travelers.",
            }
        )
    return places


def get_events(city: str, date: str) -> list[dict]:
    rng = _seeded_rng("events", city, date)
    count = rng.randint(0, 3)
    events = []
    for i in range(count):
        template = rng.choice(EVENT_TEMPLATES)
        events.append(
            {
                "name": template.format(city=city),
                "time": f"{rng.randint(17, 21)}:00",
                "description": f"A local {city} event happening on {date}.",
            }
        )
    return events


def get_restaurants(city: str, cuisine: str = "Local", price: str = "$$") -> list[dict]:
    rng = _seeded_rng("restaurants", city, cuisine, price)
    count = 3
    restaurants = []
    for i in range(count):
        name = f"{rng.choice(PLACE_ADJECTIVES)} {city} {rng.choice(PLACE_NOUNS['restaurant'])}"
        restaurants.append(
            {
                "name": name,
                "cuisine": cuisine if cuisine != "Local" else rng.choice(CUISINES),
                "price": price,
                "rating": round(rng.uniform(3.5, 5.0), 1),
            }
        )
    return restaurants
