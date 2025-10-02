import logging
from typing import Iterable, Set
from fastapi import Request, Response

def build_http_error_mirror(
    *,
    logger_name: str = "uvicorn.error",
    skip_prefixes: Iterable[str] = ("/static/", "/media/"),
) -> callable:
    """
    Gibt eine HTTP-Middleware-Funktion zurück, die alle 4xx/5xx
    zusätzlich in logger_name schreibt (z.B. web_error.log via Handler).
    """
    skip: Set[str] = set(skip_prefixes)
    log = logging.getLogger(logger_name)

    async def middleware(request: Request, call_next) -> Response:
        if any(request.url.path.startswith(p) for p in skip):
            return await call_next(request)

        response = await call_next(request)

        if response.status_code >= 400:
            lvl = logging.ERROR if response.status_code >= 500 else logging.WARNING
            ua  = request.headers.get("user-agent", "-")
            ref = request.headers.get("referer", "-")
            ip  = getattr(request.client, "host", "-")
            q   = str(request.query_params) if request.query_params else ""
            log.log(
                lvl,
                '%s %s %s -> %d (ip=%s, ua="%s", ref="%s")',
                request.method,
                request.url.path,
                q,
                response.status_code,
                ip,
                ua,
                ref,
            )
        return response

    return middleware
