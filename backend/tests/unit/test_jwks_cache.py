"""The JWKS cache: when we ask Google for its public keys, and when we may use what we already have.

Two opposing pressures. Fetching on every login is slow and makes Google's key endpoint a hard
dependency of signing in. Caching for ever means missing a key rotation. The policy here leans on
one fact: a cached key can only be *used* if the token's `kid` matches it, so serving a stale cache
is safe -- the worst case is that the key we need is missing, and then we fail closed.
"""

import asyncio

import httpx
import pytest

from app.auth.jwks import (
    FALLBACK_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    STALE_GRACE_SECONDS,
    UNKNOWN_KID_COOLDOWN_SECONDS,
    JwksCache,
    NoUsableKey,
    cache_ttl_from_headers,
)
from tests.google_fakes import (
    CURRENT_JWKS,
    KID,
    ROTATED_JWKS,
    SIGNING_KEY,
    WRONG_KID,
    FakeJwksEndpoint,
    jwks_document,
)


class Clock:
    """A clock the test moves by hand, so nothing waits and nothing is flaky."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(google: FakeJwksEndpoint, clock: Clock) -> tuple[JwksCache, httpx.AsyncClient]:
    client = google.client()
    return JwksCache(client=client, clock=clock), client


# --- how long a fetched key set stays fresh --------------------------------------------------


def test_the_ttl_comes_from_googles_cache_control_header() -> None:
    assert (
        cache_ttl_from_headers({"cache-control": "public, max-age=7200, must-revalidate"}) == 7200
    )


def test_time_the_response_already_spent_in_a_proxy_is_subtracted() -> None:
    """`Age: 600` means this response is already ten minutes old; it is fresh for that much less."""
    assert cache_ttl_from_headers({"cache-control": "max-age=3600", "age": "600"}) == 3000


@pytest.mark.parametrize(
    "headers",
    [{}, {"cache-control": "public"}, {"cache-control": "max-age=abc"}, {"cache-control": ""}],
    ids=["absent", "no-max-age", "unparseable", "empty"],
)
def test_an_unusable_header_falls_back_to_one_hour(headers: dict[str, str]) -> None:
    assert cache_ttl_from_headers(headers) == FALLBACK_TTL_SECONDS == 3600


@pytest.mark.parametrize("directive", ["no-store", "no-cache", "max-age=0"])
def test_google_asking_us_not_to_cache_gives_the_shortest_allowed_ttl(directive: str) -> None:
    """We still cache briefly: re-fetching per login would be a self-inflicted outage risk."""
    assert cache_ttl_from_headers({"cache-control": directive}) == MIN_TTL_SECONDS


def test_the_ttl_is_clamped_at_both_ends() -> None:
    assert cache_ttl_from_headers({"cache-control": "max-age=5"}) == MIN_TTL_SECONDS == 300
    biggest = cache_ttl_from_headers({"cache-control": "max-age=99999999"})
    assert biggest == MAX_TTL_SECONDS == 86400


def test_a_nonsense_age_header_is_ignored_rather_than_crashing() -> None:
    assert cache_ttl_from_headers({"cache-control": "max-age=3600", "age": "later"}) == 3600


# --- fetching, and not re-fetching -----------------------------------------------------------


async def test_the_first_lookup_fetches_the_keys() -> None:
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        assert await cache.get_key(KID) is not None
    assert google.calls == 1


async def test_later_lookups_within_the_ttl_reuse_the_cache() -> None:
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        for _ in range(5):
            await cache.get_key(KID)
        clock.advance(3599)
        await cache.get_key(KID)
    assert google.calls == 1


async def test_once_the_ttl_has_passed_the_next_lookup_refetches() -> None:
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        clock.advance(3601)
        await cache.get_key(KID)
    assert google.calls == 2


async def test_twenty_simultaneous_lookups_on_a_cold_cache_fetch_once() -> None:
    """Single-flight: a burst of logins must not become a burst of requests to Google."""
    google, clock = FakeJwksEndpoint(), Clock()
    google.on_request = lambda: asyncio.sleep(0.05)  # a slow fetch widens the race window
    cache, client = build(google, clock)
    async with client:
        keys = await asyncio.gather(*(cache.get_key(KID) for _ in range(20)))
    assert google.calls == 1
    assert all(key is not None for key in keys)


# --- a key we have never seen (the rotation case) ---------------------------------------------


async def test_an_unknown_kid_forces_one_refresh_and_then_succeeds() -> None:
    """Google rotated and our cache is merely out of date: refresh, then the key is there."""
    google, clock = FakeJwksEndpoint(jwks_document()), Clock()
    google.document = CURRENT_JWKS
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.document = ROTATED_JWKS  # Google publishes a new key alongside the old
        assert await cache.get_key(WRONG_KID) is not None
    assert google.calls == 2  # the second call is the forced refresh


async def test_an_unknown_kid_that_is_still_unknown_after_a_refresh_fails() -> None:
    """A forged or simply wrong `kid`. We refresh once, learn nothing, and refuse."""
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        with pytest.raises(NoUsableKey):
            await cache.get_key("no-such-key")
    assert google.calls == 2


async def test_a_flood_of_unknown_kids_refreshes_only_once_per_cooldown() -> None:
    """Otherwise anyone could turn our login endpoint into a load generator aimed at Google."""
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        for index in range(25):
            with pytest.raises(NoUsableKey):
                await cache.get_key(f"forged-{index}")
    assert google.calls == 2  # the initial fetch, plus exactly one forced refresh


async def test_after_the_cooldown_a_new_unknown_kid_may_refresh_again() -> None:
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        with pytest.raises(NoUsableKey):
            await cache.get_key("forged-1")
        clock.advance(UNKNOWN_KID_COOLDOWN_SECONDS + 1)
        google.document = ROTATED_JWKS
        assert await cache.get_key(WRONG_KID) is not None
    assert google.calls == 3


# --- Google unavailable ------------------------------------------------------------------------


async def test_a_fresh_cache_is_used_without_noticing_that_google_is_down() -> None:
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down()
        clock.advance(600)
        assert await cache.get_key(KID) is not None
    assert google.calls == 1  # never even tried: the cache was still fresh


async def test_a_stale_cache_is_still_used_when_google_cannot_be_reached() -> None:
    """The approved policy: availability wins, because a stale key is still a real Google key."""
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down()
        clock.advance(3600 + 60)  # past the TTL, well inside the grace period
        assert await cache.get_key(KID) is not None
    assert google.calls == 2  # it did try to refresh first


@pytest.mark.parametrize(
    "failure",
    [None, httpx.ReadTimeout("timeout"), httpx.ConnectError("refused")],
    ids=["connect-error", "timeout", "refused"],
)
async def test_every_kind_of_network_failure_behaves_the_same(failure: Exception | None) -> None:
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down(failure)
        clock.advance(3700)
        assert await cache.get_key(KID) is not None


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_an_http_error_from_google_is_a_failure_not_a_key_set(status: int) -> None:
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        google.status = status
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)


@pytest.mark.parametrize(
    "body", ["not json at all", "{}", '{"keys": []}', '{"keys": "not-a-list"}', ""]
)
async def test_a_response_that_is_not_a_usable_key_set_is_a_failure(body: str) -> None:
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        google.body = body
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)


# --- the hard limit: when we stop trusting what we have ----------------------------------------


async def test_a_cache_past_the_grace_period_is_refused_even_though_the_kid_matches() -> None:
    """The one case where we throw away a key we hold: it is simply too old to trust."""
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down()
        clock.advance(3600 + STALE_GRACE_SECONDS + 1)
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)


async def test_the_grace_boundary_is_where_we_say_it_is() -> None:
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down()
        clock.advance(3600 + STALE_GRACE_SECONDS - 1)
        assert await cache.get_key(KID) is not None  # just inside
        clock.advance(2)
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)  # just outside


async def test_with_no_cache_at_all_a_failed_fetch_simply_fails() -> None:
    """**The security boundary**: no key means no verification, so no login. Never a bypass."""
    google, clock = FakeJwksEndpoint(), Clock()
    google.go_down()
    cache, client = build(google, clock)
    async with client:
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)


async def test_recovery_after_an_outage_needs_no_restart() -> None:
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    google.go_down()
    cache, client = build(google, clock)
    async with client:
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)
        google.come_back()
        assert await cache.get_key(KID) is not None


async def test_the_failure_says_nothing_about_google_or_the_key() -> None:
    google, clock = FakeJwksEndpoint(), Clock()
    google.go_down(httpx.ConnectError("dial tcp 10.1.2.3:443: connection refused"))
    cache, client = build(google, clock)
    async with client:
        with pytest.raises(NoUsableKey) as caught:
            await cache.get_key(KID)
    assert "10.1.2.3" not in str(caught.value)


async def test_a_stale_cache_that_lacks_the_wanted_kid_fails_rather_than_offering_another_key() -> (
    None
):
    """Falling back to "some key we happen to hold" would be verifying against the wrong key."""
    google, clock = FakeJwksEndpoint(headers={"Cache-Control": "max-age=3600"}), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down()
        clock.advance(3700)  # stale, inside the grace period, but the kid does not match
        with pytest.raises(NoUsableKey):
            await cache.get_key(WRONG_KID)
        assert await cache.get_key(KID) is not None  # the one we do hold still works


async def test_an_unknown_kid_when_google_is_unreachable_fails() -> None:
    """The refresh cannot happen, so the unknown key stays unknown: fail closed."""
    google, clock = FakeJwksEndpoint(), Clock()
    cache, client = build(google, clock)
    async with client:
        await cache.get_key(KID)
        google.go_down()
        with pytest.raises(NoUsableKey):
            await cache.get_key(WRONG_KID)


async def test_a_key_set_whose_keys_have_no_kid_is_unusable() -> None:
    """We select keys by `kid`; a set we cannot index by `kid` is no use to us."""
    google, clock = FakeJwksEndpoint(), Clock()
    document = jwks_document((SIGNING_KEY, KID))
    for key in document["keys"]:
        key.pop("kid")
    google.document = document
    cache, client = build(google, clock)
    async with client:
        with pytest.raises(NoUsableKey):
            await cache.get_key(KID)
