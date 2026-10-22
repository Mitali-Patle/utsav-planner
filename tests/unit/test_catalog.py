"""Catalog structure and coverage of SPEC §5 (4 capabilities x 3 event types)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.catalog import (
    CATALOG,
    CONTINGENCY_ID,
    EventTypeSpec,
    spec_for,
    tasks_for,
    validate_sub_events,
    vendors_for,
)
from app.domain.errors import InvalidInputError
from app.domain.models import EventType

ALL_TYPES = list(EventType)

# SPEC §5 vendor checklist cell, per event type, with the sub-events that enable them.
SPEC_VENDORS = {
    EventType.WEDDING: (
        ["ceremony", "mehendi"],
        {"caterer", "decorator", "mandap", "photographer", "mehendi_artist", "music", "pandit"},
    ),
    EventType.BIRTHDAY: ([], {"cake_shop", "decorator", "entertainer", "venue"}),
    EventType.COMMUNITY: (
        ["puja"],
        {"pandal_contractor", "sound_lighting", "caterer", "idol_maker", "waste_management"},
    ),
}


def test_every_event_type_has_a_catalog_entry() -> None:
    assert set(CATALOG) == set(ALL_TYPES)


@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_each_type_covers_all_four_capabilities(event_type: EventType) -> None:
    spec = spec_for(event_type)
    assert len(spec.categories) >= 8, "budgets"
    assert len(spec.tasks) >= 10, "timelines"
    assert len(spec.vendors) >= 4, "vendor checklists"
    assert len(spec.fallback_ideas) >= 3, "personalized ideas (non-AI fallback)"
    assert len(spec.sub_events) >= 3


@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_spec_vendor_checklist_cell_is_covered(event_type: EventType) -> None:
    sub_events, expected = SPEC_VENDORS[event_type]
    available = {v.vendor_type for v in vendors_for(event_type, sub_events)}
    assert expected <= available


@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_contingency_is_five_to_ten_percent(event_type: EventType) -> None:
    categories = spec_for(event_type).categories
    contingency = next(c.weight for c in categories if c.id == CONTINGENCY_ID)
    share = contingency / sum(c.weight for c in categories)
    assert 0.05 <= share <= 0.10


@pytest.mark.parametrize("event_type", ALL_TYPES)
def test_catering_scales_with_guests(event_type: EventType) -> None:
    scaled = {c.id for c in spec_for(event_type).categories if c.scales_with_guests}
    assert any("catering" in category_id for category_id in scaled)


def test_timelines_match_event_scale() -> None:
    def earliest(event_type: EventType) -> int:
        return max(t.offset_days for t in spec_for(event_type).tasks)

    assert earliest(EventType.WEDDING) >= 120
    assert 21 <= earliest(EventType.BIRTHDAY) <= 45
    assert 45 <= earliest(EventType.COMMUNITY) <= 90


def test_community_events_include_permits_and_cleanup_after_the_event() -> None:
    tasks = {t.key: t for t in spec_for(EventType.COMMUNITY).tasks}
    assert tasks["apply_permits"].offset_days >= 30
    assert tasks["clean_venue"].offset_days < 0


def test_ideas_explain_why() -> None:
    for spec in CATALOG.values():
        for idea in spec.fallback_ideas:
            assert len(idea.why) > 20


def test_validate_sub_events_dedupes_in_catalog_order() -> None:
    chosen = validate_sub_events(EventType.WEDDING, ["reception", "haldi", "haldi"])
    assert chosen == ("haldi", "reception")
    assert validate_sub_events(EventType.BIRTHDAY, []) == ()


def test_unknown_sub_event_is_rejected() -> None:
    with pytest.raises(InvalidInputError) as error:
        validate_sub_events(EventType.BIRTHDAY, ["sangeet"])
    assert error.value.code == "unknown_sub_event"


def test_sub_event_tasks_and_vendors_appear_only_when_chosen() -> None:
    without = {t.key for t in tasks_for(EventType.WEDDING, [])}
    with_mehendi = {t.key for t in tasks_for(EventType.WEDDING, ["mehendi"])}
    assert "book_mehendi" not in without
    assert "book_mehendi" in with_mehendi
    assert "mehendi_artist" not in {v.vendor_type for v in vendors_for(EventType.WEDDING, [])}


def _wedding_data() -> dict[str, Any]:
    return spec_for(EventType.WEDDING).model_dump()


def test_spec_rejects_unknown_category_reference() -> None:
    data = _wedding_data()
    data["vendors"] = [
        *data["vendors"],
        {**data["vendors"][0], "vendor_type": "x", "category_id": "nope"},
    ]
    with pytest.raises(ValidationError, match="unknown category nope"):
        EventTypeSpec.model_validate(data)


def test_spec_rejects_duplicate_ids() -> None:
    data = _wedding_data()
    data["tasks"] = [*data["tasks"], data["tasks"][0]]
    with pytest.raises(ValidationError, match="duplicate task id"):
        EventTypeSpec.model_validate(data)


def test_spec_requires_contingency() -> None:
    data = _wedding_data()
    data["categories"] = [c for c in data["categories"] if c["id"] != "contingency"]
    with pytest.raises(ValidationError, match="contingency"):
        EventTypeSpec.model_validate(data)
