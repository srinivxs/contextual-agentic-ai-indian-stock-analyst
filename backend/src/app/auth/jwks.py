"""Google's public signing keys, cached.

Google signs ID tokens with a private key and publishes the matching public keys as a JWKS (JSON
Web Key Set). We need one of those keys to check a signature. Fetching them on every login would be
slow and would make Google's key endpoint a hard dependency of signing in, so we cache.

The policy leans on one fact: **a cached key is only ever used when the token's `kid` names it.** So
keeping an old key is safe; the worst case is that the key we need is missing, and then we fail
closed. That is why a stale cache may still serve a matching key while Google is unreachable, and
why we never fall back to "some other key" or skip verification.

These constants are security policy, not deployment configuration, so they live here rather than in
``Settings``.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx
from jwt import PyJWK, PyJWKSet

from app.auth.google import GOOGLE_JWKS_URI

logger = logging.getLogger("app.auth.jwks")

# How long a freshly fetched key set counts as fresh, clamped around whatever Google asks for.
MIN_TTL_SECONDS = 300  # 5 minutes: refetching per login would be a self-inflicted outage risk
MAX_TTL_SECONDS = 86400  # 24 hours
FALLBACK_TTL_SECONDS = 3600  # used when Cache-Control tells us nothing usable
# After the TTL expires we may still use a cached key for this long, but only if Google cannot be
# reached AND the wanted `kid` is in that cache. Maximum usable age is therefore TTL + this.
STALE_GRACE_SECONDS = 86400  # 24 hours
# A token naming an unknown `kid` triggers one refresh; this stops a flood of forged `kid`s from
# turning our login endpoint into a load generator aimed at Google.
UNKNOWN_KID_COOLDOWN_SECONDS = 60

_MAX_AGE = re.compile(r"max-age\s*=\s*(\d+)")
_NO_CACHING = re.compile(r"\bno-store\b|\bno-cache\b")


class NoUsableKey(Exception):
    """We hold no key that could verify this token, so verification cannot even be attempted.

    One message for every cause (never fetched, Google unreachable, unknown `kid`, cache too old),
    so nothing about our internal state reaches the caller.
    """


def cache_ttl_from_headers(headers: Mapping[str, str]) -> float:
    """How long to treat this response as fresh, from its own cache directives.

    Google documents that its keys change infrequently and that callers should cache them using the
    cache directives of the response, so we read them rather than hardcoding a number. ``Age`` is
    subtracted because a response that already sat in a proxy is that much less fresh.
    """
    directives = (headers.get("cache-control") or "").lower()
    if _NO_CACHING.search(directives):
        return MIN_TTL_SECONDS
    found = _MAX_AGE.search(directives)
    if found is None:
        return FALLBACK_TTL_SECONDS
    try:
        already_spent = int(headers.get("age") or 0)
    except ValueError:
        already_spent = 0  # a malformed Age header is ignored, never fatal
    return _clamp(int(found.group(1)) - already_spent)


def _clamp(seconds: float) -> float:
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, seconds))


@dataclass(frozen=True)
class _CachedKeys:
    keyset: PyJWKSet
    kids: frozenset[str]
    fetched_at: float
    ttl: float


class JwksCache:
    """One cache per process, shared by every request. Not a singleton: the app owns an instance.

    ``clock`` is monotonic on purpose: cache age must not jump when the system clock is corrected.
    Token expiry uses the wall clock instead, in ``id_token``.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        uri: str = GOOGLE_JWKS_URI,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._uri = uri
        self._clock = clock
        self._cached: _CachedKeys | None = None
        self._lock = asyncio.Lock()  # single-flight: a burst of logins makes one request
        self._last_forced_refresh: float | None = None

    async def get_key(self, kid: str) -> PyJWK:
        """The public key Google published under `kid`, or raise ``NoUsableKey``."""
        cached = self._cached
        if cached is not None and self._age(cached) < cached.ttl:
            if kid in cached.kids:
                return cached.keyset[kid]
            # Fresh, but we have never seen this key: Google may have rotated since we fetched.
            return await self._refresh_for_unknown_kid(kid)

        # No cache at all, or it is past its TTL.
        if await self._refresh():
            return self._from_current(kid)
        return self._from_stale(kid, cached)

    def _age(self, cached: _CachedKeys) -> float:
        return self._clock() - cached.fetched_at

    def _from_current(self, kid: str) -> PyJWK:
        cached = self._cached
        if cached is None or kid not in cached.kids:
            # We have just refreshed, so this really is a key Google does not publish.
            raise NoUsableKey("no usable signing key")
        return cached.keyset[kid]

    def _from_stale(self, kid: str, cached: _CachedKeys | None) -> PyJWK:
        """The refresh failed. Fall back to what we hold, if it is recent enough and fits."""
        if cached is None:
            raise NoUsableKey("no usable signing key")  # nothing to fall back to: fail closed
        age = self._age(cached)
        if age - cached.ttl > STALE_GRACE_SECONDS:
            raise NoUsableKey("no usable signing key")  # too old to trust
        if kid not in cached.kids:
            raise NoUsableKey("no usable signing key")  # never another key: only this exact one
        logger.warning("jwks_serving_stale_keys", extra={"age_seconds": round(age)})
        return cached.keyset[kid]

    async def _refresh_for_unknown_kid(self, kid: str) -> PyJWK:
        now = self._clock()
        last = self._last_forced_refresh
        if last is not None and now - last < UNKNOWN_KID_COOLDOWN_SECONDS:
            raise NoUsableKey("no usable signing key")
        # Recorded before awaiting, so concurrent unknown `kid`s do not all get through.
        self._last_forced_refresh = now
        await self._refresh()
        return self._from_current(kid)

    async def _refresh(self) -> bool:
        """Fetch a new key set. Returns False if Google could not be reached or made sense."""
        before = self._cached
        async with self._lock:
            if self._cached is not before:
                return True  # another coroutine refreshed while we waited for the lock
            fetched = await self._fetch()
            if fetched is None:
                return False
            self._cached = fetched
            return True

    async def _fetch(self) -> _CachedKeys | None:
        try:
            response = await self._client.get(self._uri)
            response.raise_for_status()
            keyset = PyJWKSet.from_dict(response.json())
        except Exception as error:
            # The type name is safe to log; the message could carry a host or address.
            logger.warning("jwks_fetch_failed", extra={"error": type(error).__name__})
            return None
        kids = frozenset(key.key_id for key in keyset.keys if key.key_id)
        if not kids:
            logger.warning("jwks_fetch_failed", extra={"error": "no_keys_with_kid"})
            return None
        return _CachedKeys(
            keyset=keyset,
            kids=kids,
            fetched_at=self._clock(),
            ttl=cache_ttl_from_headers(response.headers),
        )
