"""Use cases that connect the domain, the AI layer and persistence."""

from dataclasses import dataclass

from sqlalchemy import Engine

from app.ai.cache import MemoryCache, TieredCache
from app.ai.chain import FallbackChain
from app.ai.providers import build_providers
from app.config import Settings
from app.domain.clock import Clock
from app.persistence.repositories import Database, SqlCacheStore
from app.services.alternatives import AlternativesService
from app.services.change_manager import ChangeManager
from app.services.edits import PlanEditor
from app.services.planning import PlanningService


@dataclass(frozen=True)
class Services:
    """Everything the web layer calls."""

    planning: PlanningService
    changes: ChangeManager
    edits: PlanEditor
    alternatives: AlternativesService


def build_services(
    settings: Settings, engine: Engine, clock: Clock, ai: FallbackChain | None = None
) -> Services:
    """Wire the services for the app.

    Args:
        settings: Application settings (LLM chain and keys).
        engine: Database engine.
        clock: Source of today's date in India.
        ai: Fallback chain to use; built from settings when omitted (tests pass a fake).

    Returns:
        The wired services.
    """
    db = Database(engine)
    chain = ai or FallbackChain(
        build_providers(settings), TieredCache(MemoryCache(), SqlCacheStore(engine))
    )
    return Services(
        planning=PlanningService(db, chain, clock),
        changes=ChangeManager(db, chain, clock),
        edits=PlanEditor(db),
        alternatives=AlternativesService(db, chain, clock),
    )
