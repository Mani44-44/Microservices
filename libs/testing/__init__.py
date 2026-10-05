"""Test helpers shared by every service's test suite."""

from fastapi import FastAPI


def routes_committing_after_response(app: FastAPI, dependency) -> list[str]:
    """Routes that use *dependency* (a `get_db` with commit-after-yield)
    without `scope="function"`.

    FastAPI's default ("request") scope runs the code after `yield` only
    once the response has been sent, so the client can see a 2xx for a
    transaction that hasn't committed yet — or that then fails to commit.
    """
    offending = []
    for route in _iter_routes(app.routes):
        dependant = getattr(route, "dependant", None)
        stack = [dependant] if dependant is not None else []
        while stack:
            current = stack.pop()
            for sub in current.dependencies:
                if sub.call is dependency and sub.scope != "function":
                    offending.append(f"{','.join(sorted(getattr(route, 'methods', []) or []))} {route.path}")
                stack.append(sub)
    return offending


def _iter_routes(routes):
    """Flatten routes, descending into included routers (newer FastAPI
    versions keep them as wrapper entries in `app.routes`)."""
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            yield from _iter_routes(included.routes)
        else:
            yield route
