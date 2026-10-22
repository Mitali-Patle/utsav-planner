"""Repositories: ownership, versioned saves, single decisions, cache expiry and rate limits."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.dialects import postgresql

from app.ai.cache import MemoryCache, TieredCache
from app.domain.changes import ChangeKind, ChangeOperation, ChangeSet, Proposal, propose
from app.domain.errors import NotFoundError, StaleProposalError
from app.persistence.db import make_engine
from app.persistence.repositories import (
    Database,
    ProposalRepository,
    ProposalStatus,
    RateLimiter,
    SqlCacheStore,
    owner_key,
)
from app.persistence.tables import events, metadata
from tests.factories import make_plan

START = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)


class Now:
    def __init__(self) -> None:
        self.moment = START

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, **delta: float) -> None:
        self.moment += timedelta(**delta)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine("sqlite://")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def now() -> Now:
    return Now()


def a_proposal(total: int = 12_000) -> Proposal:
    change = ChangeSet(
        operations=(ChangeOperation(kind=ChangeKind.SET_TOTAL_BUDGET, amount_inr=total),)
    )
    return propose(make_plan(), change, date(2026, 10, 22))


# Events


@pytest.fixture
def db(engine: Engine, now: Now) -> Database:
    return Database(engine, now)


def test_event_round_trip(db: Database) -> None:
    plan = make_plan()
    with db.transaction() as repos:
        created = repos.events.create("session-a", plan, ai={"provider": "gemini:x"})
    with db.transaction() as repos:
        loaded = repos.events.get(created.id, "session-a")
    assert (loaded.plan, loaded.version, loaded.ai) == (plan, 1, {"provider": "gemini:x"})
    assert loaded.updated_at == START


def test_session_ids_are_stored_hashed(db: Database, engine: Engine) -> None:
    with db.transaction() as repos:
        repos.events.create("session-a", make_plan())
    with engine.connect() as connection:
        stored = connection.execute(select(events.c.owner)).scalar_one()
    assert stored == owner_key("session-a")
    assert "session-a" not in stored


def test_another_session_cannot_read_or_save_the_event(db: Database) -> None:
    """Must-pass scenario 12 at the storage level."""
    with db.transaction() as repos:
        created = repos.events.create("session-a", make_plan())
    with db.transaction() as repos:
        with pytest.raises(NotFoundError):
            repos.events.get(created.id, "session-b")
        with pytest.raises(NotFoundError):
            repos.events.save(created.id, "session-b", make_plan(), expected_version=1)
        assert repos.events.list_for("session-b") == []


def test_save_bumps_the_version_and_keeps_ai_unless_replaced(db: Database, now: Now) -> None:
    with db.transaction() as repos:
        created = repos.events.create("s", make_plan(), ai={"summary": "first"})
    now.advance(minutes=5)
    with db.transaction() as repos:
        saved = repos.events.save(created.id, "s", make_plan(guest_count=40), expected_version=1)
        assert (saved.version, saved.plan.version, saved.ai) == (2, 2, {"summary": "first"})
        loaded = repos.events.get(created.id, "s")
        assert (loaded.version, loaded.plan.guest_count) == (2, 40)
        assert loaded.updated_at == START + timedelta(minutes=5)
        replaced = repos.events.save(
            created.id, "s", loaded.plan, expected_version=2, ai={"summary": "second"}
        )
    assert replaced.ai == {"summary": "second"}


def test_saving_an_old_version_is_refused(db: Database) -> None:
    """Two tabs approving at once: the second one is stale (scenario 13)."""
    with db.transaction() as repos:
        created = repos.events.create("s", make_plan())
    with db.transaction() as repos:
        repos.events.save(created.id, "s", make_plan(), expected_version=1)
    with db.transaction() as repos, pytest.raises(StaleProposalError):
        repos.events.save(created.id, "s", make_plan(), expected_version=1)


def test_a_failed_unit_of_work_changes_nothing(db: Database) -> None:
    """Saving a version and deciding its proposal commit together or not at all."""
    with db.transaction() as repos:
        created = repos.events.create("s", make_plan())
        stored = repos.proposals.add(created.id, "budget 12k", {}, a_proposal())
    with pytest.raises(RuntimeError), db.transaction() as repos:
        repos.events.save(created.id, "s", make_plan(guest_count=99), expected_version=1)
        repos.proposals.decide(stored.id, created.id, ProposalStatus.APPROVED)
        raise RuntimeError("crash before commit")
    with db.transaction() as repos:
        assert repos.events.get(created.id, "s").version == 1
        assert repos.proposals.get(stored.id, created.id).status is ProposalStatus.PENDING


def test_list_is_newest_first(db: Database, now: Now) -> None:
    with db.transaction() as repos:
        first = repos.events.create("s", make_plan())
        now.advance(minutes=1)
        second = repos.events.create("s", make_plan())
        assert [e.id for e in repos.events.list_for("s")] == [second.id, first.id]


def test_unknown_event_is_not_found(db: Database) -> None:
    with db.transaction() as repos, pytest.raises(NotFoundError):
        repos.events.get("missing", "s")


# Proposals


def test_proposal_round_trip_and_single_decision(db: Database, now: Now) -> None:
    proposal = a_proposal()
    with db.transaction() as repos:
        event = repos.events.create("s", make_plan())
        stored = repos.proposals.add(event.id, "budget 12k", {"status": "ok"}, proposal)
    with db.transaction() as repos:
        loaded = repos.proposals.get(stored.id, event.id)
    assert (loaded.proposal, loaded.status, loaded.request_text) == (
        proposal,
        ProposalStatus.PENDING,
        "budget 12k",
    )
    now.advance(seconds=30)
    with db.transaction() as repos:
        assert repos.proposals.decide(stored.id, event.id, ProposalStatus.APPROVED)
    with db.transaction() as repos:
        assert not repos.proposals.decide(stored.id, event.id, ProposalStatus.APPROVED), (
            "approving twice is refused"
        )
        decided = repos.proposals.get(stored.id, event.id)
    assert (decided.status, decided.decided_at) == (
        ProposalStatus.APPROVED,
        START + timedelta(seconds=30),
    )


def test_proposals_are_scoped_to_their_event(db: Database) -> None:
    with db.transaction() as repos:
        mine = repos.events.create("s", make_plan())
        other = repos.events.create("s", make_plan())
        stored = repos.proposals.add(mine.id, "x", {}, a_proposal())
        with pytest.raises(NotFoundError):
            repos.proposals.get(stored.id, other.id)
        assert not repos.proposals.decide(stored.id, other.id, ProposalStatus.REJECTED)


def test_history_is_newest_first(db: Database, now: Now) -> None:
    with db.transaction() as repos:
        event = repos.events.create("s", make_plan())
        first = repos.proposals.add(event.id, "first", {}, a_proposal(12_000))
        now.advance(minutes=1)
        second = repos.proposals.add(event.id, "second", {}, a_proposal(13_000))
        assert [p.id for p in repos.proposals.history(event.id)] == [second.id, first.id]


def test_request_text_is_capped(db: Database) -> None:
    with db.transaction() as repos:
        event = repos.events.create("s", make_plan())
        stored = repos.proposals.add(event.id, "x" * 400, {}, a_proposal())
    assert len(stored.request_text) == 300


# AI cache


def test_sql_cache_stores_replaces_and_expires(engine: Engine, now: Now) -> None:
    cache = SqlCacheStore(engine, now)
    assert cache.get("k") is None
    cache.set("k", {"v": 1}, ttl_s=60)
    cache.set("k", {"v": 2}, ttl_s=60)
    assert cache.get("k") == {"v": 2}
    now.advance(seconds=61)
    assert cache.get("k") is None


def test_sql_cache_purges_expired_rows(engine: Engine, now: Now) -> None:
    cache = SqlCacheStore(engine, now)
    cache.set("old", {"v": 0}, ttl_s=1)
    now.advance(seconds=5)
    for index in range(49):
        cache.set(f"k{index}", {"v": index}, ttl_s=600)
    with engine.connect() as connection:
        keys = set(connection.execute(select(metadata.tables["ai_cache"].c.key)).scalars())
    assert "old" not in keys
    assert len(keys) == 49


def test_tiered_cache_refills_the_fast_tier(engine: Engine, now: Now) -> None:
    shared = SqlCacheStore(engine, now)
    fast = MemoryCache()
    shared.set("k", {"v": 1}, ttl_s=600)
    tiered = TieredCache(fast, shared)
    assert tiered.get("k") == {"v": 1}
    assert fast.get("k") == {"v": 1}
    shared.set("k", {"v": 99}, ttl_s=600)
    assert tiered.get("k") == {"v": 1}, "served from the fast tier"
    tiered.set("n", {"v": 2}, ttl_s=600)
    assert (fast.get("n"), shared.get("n")) == ({"v": 2}, {"v": 2})
    assert tiered.get("missing") is None


# Rate limits


def test_rate_limit_counts_per_window_and_bucket(engine: Engine, now: Now) -> None:
    limiter = RateLimiter(engine, now)
    assert [limiter.hit("ip:a", limit=2, window_s=600) for _ in range(3)] == [True, True, False]
    assert limiter.hit("ip:b", limit=2, window_s=600), "buckets are independent"
    now.advance(seconds=600)
    assert limiter.hit("ip:a", limit=2, window_s=600), "a new window starts fresh"


def test_rate_limit_purges_old_windows(engine: Engine, now: Now) -> None:
    limiter = RateLimiter(engine, now)
    limiter.hit("old", limit=10, window_s=60)
    now.advance(days=3)
    for index in range(49):
        limiter.hit(f"b{index}", limit=10, window_s=60)
    with engine.connect() as connection:
        buckets = set(connection.execute(select(metadata.tables["rate_limits"].c.bucket)).scalars())
    assert "old" not in buckets


def test_approval_reads_the_proposal_with_a_row_lock_on_postgres() -> None:
    """`lock=True` must become SELECT ... FOR UPDATE, so a concurrent reject waits."""

    class Recorder:
        statement: Any = None

        def execute(self, statement: Any) -> "Recorder":
            self.statement = statement
            return self

        def one_or_none(self) -> None:
            return None

    recorder = Recorder()
    with pytest.raises(NotFoundError):
        ProposalRepository(cast(Any, recorder)).get("p-1", "e-1", lock=True)
    compiled = str(recorder.statement.compile(dialect=cast(Any, postgresql.dialect)()))
    assert compiled.rstrip().endswith("FOR UPDATE")
