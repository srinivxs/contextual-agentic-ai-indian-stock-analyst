"""The worker's only way onto the internet (ADR 018): honest, bounded, allow-listed at every hop.

* It says who we are: a plain User-Agent naming the project and how to reach its owner. No browser
  disguise, no rotating identities, no cookies. If a site refuses that, we stop (the project notes: no
  anti-bot workarounds).
* Every address, including each redirect target, must pass the caller's ``allowed`` check BEFORE
  it is requested. A redirect elsewhere (a look-alike host, a cloud metadata address) is refused.
* It reads at most ``limit`` bytes, streaming, and gives up after the client's timeout.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

USER_AGENT = (
    "stock-analyst-portfolio-project/0.1 "
    "(personal research on 3 stocks; https://github.com/srinivxs)"
)
MAX_REDIRECTS = 3


class FetchRefused(Exception):
    """The address, or a redirect target, is not allowed. Nothing was requested from it."""


class FetchTooLarge(Exception):
    """The body passed the limit. Reading stopped there."""


@dataclass(frozen=True)
class Fetched:
    status: int
    headers: httpx.Headers
    body: bytes


async def polite_fetch(
    http: httpx.AsyncClient,
    url: str,
    *,
    allowed: Callable[[str], bool],
    limit: int,
    extra_headers: Mapping[str, str] | None = None,
    not_modified_ok: bool = False,
) -> Fetched:
    """The same safe GET, also returning the status and headers. ``extra_headers`` (for example
    If-None-Match) are added to the User-Agent; with ``not_modified_ok`` a 304 is a result, not an
    error."""
    headers = {**(extra_headers or {}), "User-Agent": USER_AGENT}
    for _ in range(MAX_REDIRECTS + 1):
        if not allowed(url):
            raise FetchRefused(f"not an allowed address: {url[:200]}")
        request = http.build_request("GET", url, headers=headers)
        response = await http.send(request, stream=True, follow_redirects=False)
        try:
            if not_modified_ok and response.status_code == 304:
                return Fetched(304, response.headers, b"")
            if response.is_redirect:
                url = str(response.next_request.url) if response.next_request else ""
                continue
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise FetchTooLarge(f"more than {limit} bytes")
            return Fetched(response.status_code, response.headers, bytes(body))
        finally:
            await response.aclose()
    raise FetchRefused(f"more than {MAX_REDIRECTS} redirects")


async def polite_get(
    http: httpx.AsyncClient, url: str, *, allowed: Callable[[str], bool], limit: int
) -> bytes:
    return (await polite_fetch(http, url, allowed=allowed, limit=limit)).body
