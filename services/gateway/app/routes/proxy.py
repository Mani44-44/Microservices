"""
Generic reverse-proxy helper used by all route modules.

Forwards the original request (method, headers, body, query params)
to the specified target URL and streams the response back verbatim.
Host header is stripped so the downstream service sees its own host.
"""

import httpx
from fastapi import Request
from fastapi.responses import Response

from libs.security import internal_headers


async def proxy_request(
    request: Request,
    target_url: str,
    client: httpx.AsyncClient,
    extra_headers: dict | None = None,
) -> Response:
    """
    Forward *request* to *target_url* via *client*.

    Args:
        request:       The incoming FastAPI/Starlette request object.
        target_url:    Fully-qualified URL of the downstream endpoint.
        client:        Shared ``httpx.AsyncClient`` from app state.
        extra_headers: Optional dict of headers injected/overridden before
                       forwarding (e.g. ``X-User-ID``).

    Returns:
        A FastAPI ``Response`` with the downstream status, headers, and body.
    """
    # Copy all headers; drop 'host' so httpx sets the correct one.
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)  # let httpx recalculate

    headers.update(internal_headers())
    if extra_headers:
        headers.update(extra_headers)

    body = await request.body()

    try:
        upstream = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
    except httpx.TimeoutException:
        return Response(
            content=b'{"error":{"code":"UPSTREAM_TIMEOUT","message":"Upstream service timed out"}}',
            status_code=504,
            media_type="application/json",
        )
    except httpx.RequestError as exc:
        return Response(
            content=b'{"error":{"code":"UPSTREAM_UNAVAILABLE","message":"Upstream service unavailable"}}',
            status_code=502,
            media_type="application/json",
        )

    # Propagate all headers except ones that cause issues when re-forwarded.
    excluded = {"transfer-encoding", "content-encoding"}
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in excluded
    }

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
