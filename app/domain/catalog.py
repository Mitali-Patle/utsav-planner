"""Planning catalog for Indian weddings, birthdays and community events.

This is the expert baseline the app starts from: budget categories with relative weights,
standard tasks with deadlines counted back from the event, vendor types with booking lead
times, optional sub-events that shift the budget, and non-AI fallback ideas. The AI
personalizes on top of it; code turns it into rupees and dates.

The numbers are planning heuristics, not facts. Weights use the midpoints of published
ranges where they exist; users can change every amount and date. Sources (mostly
planning-industry guides, whose ranges vary):

- Wedding budget split (venue 20-30 %, catering 20-30 %, decor 10-16 %, photo 5-10 %):
  https://samaro.ai/blogs/how-to-plan-wedding-budget-india-2025 ,
  https://www.manyavar.com/en-in/blog-wedding-budget-planning-guide-for-indian-couples.html ,
  https://sum.money/in/marriage-budget-calculator/ (based on WeddingWire India and
  WedMeGood data), https://www.bajajfinserv.in/average-indian-wedding-cost
- Wedding timeline (venue and date first, vendors 9-12 months, invitations 3-6 months):
  https://www.manyavar.com/en-in/blog-wedding-planning-checklist-guide.html ,
  https://venuelook.com/blog/plan-like-a-pro-the-essential-indian-wedding-checklist-from-start-to-finish
- Birthday timeline (venue and entertainers 4-6 weeks, invitations 3-4 weeks, cake about a
  week or more for custom cakes): https://www.cozi.com/blog/complete-kids-birthday-party-checklist ,
  https://patisserie-valerie.co.uk/blogs/news/how-far-in-advance-should-you-order-a-celebration-cake
- Community-event permissions (police, municipal, fire and loudspeaker NOCs; windows from
  about 20 days to 2 months depending on the city):
  https://hindutone.com/festivals/ganesh-chaturthi/a-complete-guide-to-permissions-for-setting-up-ganesh-mandapams-in-india/
  and
  https://www.freepressjournal.in/mumbai/get-permission-for-ganesh-pandals-online

Wedding lead times start at about six months rather than the 9-12 months some guides
suggest, because many families plan within a few months. Tasks whose deadline has already
passed are flagged as compressed rather than dropped.
"""

from collections.abc import Sequence
from types import MappingProxyType
from typing import Self

from pydantic import Field, model_validator

from app.domain.errors import InvalidInputError
from app.domain.models import (
    MAX_DAYS_AFTER,
    MAX_DAYS_AHEAD,
    MAX_LEAD_DAYS,
    BudgetImpact,
    CategoryId,
    CategoryRef,
    Description,
    DomainModel,
    EventType,
    Rationale,
    ShortText,
    Slug,
)

CONTINGENCY_ID = CategoryId("contingency")


class CategorySpec(DomainModel):
    """A baseline budget category."""

    id: CategoryRef
    name: ShortText
    weight: int = Field(ge=1)
    scales_with_guests: bool = False


class TaskSpec(DomainModel):
    """A standard task; `offset_days` is days before the event (negative: after it)."""

    key: Slug
    title: ShortText
    category_id: CategoryRef | None = None
    offset_days: int = Field(ge=-MAX_DAYS_AFTER, le=MAX_DAYS_AHEAD)
    why: Rationale | None = None


class VendorSpec(DomainModel):
    """A vendor type and how many days ahead it should be booked."""

    vendor_type: Slug
    name: ShortText
    category_id: CategoryRef
    lead_days: int = Field(ge=0, le=MAX_LEAD_DAYS)


class WeightBonus(DomainModel):
    """Extra relative weight a sub-event adds to a category."""

    category_id: CategoryRef
    weight: int = Field(ge=1)


class IdeaSpec(DomainModel):
    """An idea before it is added to a plan: from the catalog (fallback) or from the AI."""

    title: ShortText
    description: Description
    category_id: CategoryRef | None = None
    budget_impact: BudgetImpact
    why: Rationale


class SubEventSpec(DomainModel):
    """An optional function within an event, such as a sangeet or a community meal."""

    id: Slug
    name: ShortText
    weight_bonus: tuple[WeightBonus, ...] = ()
    tasks: tuple[TaskSpec, ...] = ()
    vendors: tuple[VendorSpec, ...] = ()


class EventTypeSpec(DomainModel):
    """Everything the planner knows about one event type."""

    event_type: EventType
    categories: tuple[CategorySpec, ...] = Field(min_length=1)
    tasks: tuple[TaskSpec, ...]
    vendors: tuple[VendorSpec, ...]
    sub_events: tuple[SubEventSpec, ...]
    fallback_ideas: tuple[IdeaSpec, ...]

    @model_validator(mode="after")
    def check_references(self) -> Self:
        """Ensure ids are unique and every reference points to a category of this type.

        Returns:
            The validated spec.

        Raises:
            ValueError: On a duplicate id, an unknown category or a missing contingency line.
        """
        category_ids = [c.id for c in self.categories]
        all_tasks = [*self.tasks, *(t for s in self.sub_events for t in s.tasks)]
        all_vendors = [*self.vendors, *(v for s in self.sub_events for v in s.vendors)]
        for label, ids in (
            ("category", category_ids),
            ("task", [t.key for t in all_tasks]),
            ("vendor", [v.vendor_type for v in all_vendors]),
            ("sub-event", [s.id for s in self.sub_events]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{self.event_type}: duplicate {label} id")

        known = set(category_ids)
        references = [
            *(t.category_id for t in all_tasks if t.category_id is not None),
            *(v.category_id for v in all_vendors),
            *(b.category_id for s in self.sub_events for b in s.weight_bonus),
            *(i.category_id for i in self.fallback_ideas if i.category_id is not None),
        ]
        unknown = sorted(set(references) - known)
        if unknown:
            raise ValueError(f"{self.event_type}: unknown category {unknown[0]}")
        if CONTINGENCY_ID not in known:
            raise ValueError(f"{self.event_type}: a contingency category is required")
        return self

    def sub_event(self, sub_event_id: str) -> SubEventSpec:
        """Return a sub-event by id.

        Raises:
            InvalidInputError: If this event type has no such sub-event.
        """
        found = next((s for s in self.sub_events if s.id == sub_event_id), None)
        if found is None:
            raise InvalidInputError(
                f"'{sub_event_id}' is not a function of a {self.event_type} event.",
                code="unknown_sub_event",
            )
        return found


def _category(category_id: str, name: str, weight: int, *, scales: bool = False) -> CategorySpec:
    return CategorySpec(
        id=CategoryId(category_id), name=name, weight=weight, scales_with_guests=scales
    )


def _task(key: str, title: str, category_id: str | None, offset_days: int) -> TaskSpec:
    category = CategoryId(category_id) if category_id else None
    return TaskSpec(key=key, title=title, category_id=category, offset_days=offset_days)


def _vendor(vendor_type: str, name: str, category_id: str, lead_days: int) -> VendorSpec:
    return VendorSpec(
        vendor_type=vendor_type, name=name, category_id=CategoryId(category_id), lead_days=lead_days
    )


def _bonus(category_id: str, weight: int) -> WeightBonus:
    return WeightBonus(category_id=CategoryId(category_id), weight=weight)


def _idea(
    title: str, description: str, category_id: str | None, impact: BudgetImpact, why: str
) -> IdeaSpec:
    category = CategoryId(category_id) if category_id else None
    return IdeaSpec(
        title=title, description=description, category_id=category, budget_impact=impact, why=why
    )


_WEDDING = EventTypeSpec(
    event_type=EventType.WEDDING,
    categories=(
        _category("venue", "Venue and accommodation", 22, scales=True),
        _category("catering", "Catering", 25, scales=True),
        _category("decor", "Decor and flowers", 12),
        _category("photography", "Photography and video", 8),
        _category("music", "Music (DJ, band or nadaswaram)", 6),
        _category("attire_makeup", "Attire and makeup", 10),
        _category("rituals", "Pandit and rituals", 4),
        _category("invitations", "Invitations", 3),
        _category("gifts", "Gifts and return gifts", 4, scales=True),
        _category("transport", "Guest transport", 3),
        _category("contingency", "Contingency", 7),
    ),
    tasks=(
        _task("fix_muhurtham", "Fix the date and muhurtham with the pandit", "rituals", 180),
        _task("book_venue", "Book the venue", "venue", 180),
        _task("guest_list", "Finalise the guest list", None, 150),
        _task("book_caterer", "Book the caterer and do a menu tasting", "catering", 120),
        _task("book_photographer", "Book the photographer and videographer", "photography", 120),
        _task("book_decorator", "Book the decorator and agree the theme", "decor", 90),
        _task("buy_attire", "Shop for wedding outfits", "attire_makeup", 90),
        _task("book_makeup", "Book the makeup artist after a trial", "attire_makeup", 75),
        _task("book_music", "Book the DJ, band or nadaswaram", "music", 75),
        _task("print_invites", "Order printed and digital invitations", "invitations", 60),
        _task("send_invites", "Send invitations", "invitations", 45),
        _task("book_transport", "Arrange guest transport and stay", "transport", 45),
        _task("buy_gifts", "Buy return gifts", "gifts", 30),
        _task("final_headcount", "Give the caterer the final headcount", "catering", 10),
        _task("confirm_vendors", "Confirm every vendor and share the schedule", None, 7),
        _task("pack_samagri", "Pack the puja samagri for the rituals", "rituals", 3),
        _task("prepare_payments", "Prepare vendor payments for the day", None, 1),
        _task("settle_accounts", "Settle vendor payments and accounts", None, -7),
    ),
    vendors=(
        _vendor("venue", "Venue or banquet hall", "venue", 180),
        _vendor("pandit", "Pandit", "rituals", 120),
        _vendor("caterer", "Caterer", "catering", 120),
        _vendor("photographer", "Photographer and videographer", "photography", 120),
        _vendor("decorator", "Decorator", "decor", 90),
        _vendor("makeup_artist", "Makeup artist", "attire_makeup", 75),
        _vendor("music", "DJ, band or nadaswaram", "music", 75),
        _vendor("invitation_printer", "Invitation printer", "invitations", 60),
        _vendor("transport", "Transport provider", "transport", 45),
    ),
    sub_events=(
        SubEventSpec(
            id="haldi",
            name="Haldi",
            weight_bonus=(_bonus("decor", 2), _bonus("rituals", 1)),
            tasks=(_task("haldi_setup", "Arrange haldi paste and marigold decor", "decor", 7),),
        ),
        SubEventSpec(
            id="mehendi",
            name="Mehendi",
            weight_bonus=(_bonus("attire_makeup", 3), _bonus("decor", 1)),
            tasks=(
                _task("book_mehendi", "Book the mehendi artist", "attire_makeup", 60),
                _task("mehendi_seating", "Plan mehendi seating and music", "decor", 7),
            ),
            vendors=(_vendor("mehendi_artist", "Mehendi artist", "attire_makeup", 60),),
        ),
        SubEventSpec(
            id="sangeet",
            name="Sangeet",
            weight_bonus=(_bonus("music", 4), _bonus("decor", 1)),
            tasks=(
                _task(
                    "plan_performances", "Book a choreographer and plan performances", "music", 45
                ),
                _task("sangeet_soundcheck", "Run the sangeet sound check", "music", 1),
            ),
            vendors=(_vendor("choreographer", "Choreographer", "music", 45),),
        ),
        SubEventSpec(
            id="ceremony",
            name="Wedding ceremony",
            weight_bonus=(_bonus("rituals", 2), _bonus("decor", 1)),
            tasks=(
                _task("book_mandap", "Book the mandap setup", "decor", 60),
                _task("confirm_rituals", "Confirm ritual timings with the pandit", "rituals", 7),
            ),
            vendors=(_vendor("mandap", "Mandap decorator", "decor", 60),),
        ),
        SubEventSpec(
            id="reception",
            name="Reception",
            weight_bonus=(_bonus("catering", 3), _bonus("venue", 2), _bonus("photography", 1)),
            tasks=(
                _task("reception_menu", "Finalise the reception menu", "catering", 14),
                _task("reception_stage", "Plan the reception stage and seating", "decor", 21),
            ),
        ),
    ),
    fallback_ideas=(
        _idea(
            "Combine haldi and mehendi",
            "Hold haldi in the morning and mehendi in the evening at one venue.",
            "decor",
            BudgetImpact.SAVES,
            "One venue booking, one decor setup and one catering order instead of two.",
        ),
        _idea(
            "Seasonal local flowers",
            "Use marigold, rajnigandha and mogra instead of imported flowers.",
            "decor",
            BudgetImpact.SAVES,
            "Local flowers in season are cheaper, fresher and easy to source on the day.",
        ),
        _idea(
            "Live counters at the reception",
            "Replace part of the buffet with live chaat and dosa counters.",
            "catering",
            BudgetImpact.NEUTRAL,
            "Food is cooked fresh in smaller batches, so guests enjoy it and less is wasted.",
        ),
    ),
)

_BIRTHDAY = EventTypeSpec(
    event_type=EventType.BIRTHDAY,
    categories=(
        _category("venue", "Venue", 15, scales=True),
        _category("catering", "Food and drinks", 30, scales=True),
        _category("cake", "Cake", 10),
        _category("decor", "Decorations", 12),
        _category("entertainment", "Entertainment and games", 12),
        _category("return_gifts", "Return gifts", 8, scales=True),
        _category("photography", "Photography", 6),
        _category("contingency", "Contingency", 7),
    ),
    tasks=(
        _task("guest_list", "Set the budget and guest list", None, 30),
        _task("book_venue", "Book the venue or prepare the home space", "venue", 30),
        _task("book_entertainer", "Book the entertainer or games host", "entertainment", 21),
        _task("send_invites", "Send invitations", None, 21),
        _task("plan_theme", "Choose the theme and order decorations", "decor", 14),
        _task("book_photographer", "Book a photographer", "photography", 14),
        _task("order_cake", "Order the cake", "cake", 10),
        _task("buy_return_gifts", "Buy return gifts", "return_gifts", 10),
        _task("confirm_rsvps", "Confirm RSVPs and the headcount", None, 3),
        _task("confirm_food", "Confirm the food order", "catering", 3),
        _task("set_up_decor", "Set up decorations", "decor", 0),
        _task("thank_guests", "Pay vendors and thank guests", None, -2),
    ),
    vendors=(
        _vendor("venue", "Venue", "venue", 30),
        _vendor("caterer", "Caterer or restaurant", "catering", 21),
        _vendor("entertainer", "Entertainer", "entertainment", 21),
        _vendor("decorator", "Decorator", "decor", 14),
        _vendor("photographer", "Photographer", "photography", 14),
        _vendor("cake_shop", "Cake shop", "cake", 10),
    ),
    sub_events=(
        SubEventSpec(
            id="puja",
            name="Birthday puja or havan",
            weight_bonus=(_bonus("decor", 1), _bonus("catering", 1)),
            tasks=(_task("book_puja", "Book a pandit for the birthday puja", None, 14),),
        ),
        SubEventSpec(
            id="kids_games",
            name="Kids' games",
            weight_bonus=(_bonus("entertainment", 4), _bonus("return_gifts", 1)),
            tasks=(_task("plan_games", "Plan games and buy prizes", "entertainment", 7),),
        ),
        SubEventSpec(
            id="dinner",
            name="Dinner",
            weight_bonus=(_bonus("catering", 4), _bonus("venue", 1)),
            tasks=(_task("book_dinner", "Book the dinner caterer or restaurant", "catering", 21),),
        ),
    ),
    fallback_ideas=(
        _idea(
            "Host at home with a themed corner",
            "Decorate one corner as a photo and cake spot instead of renting a hall.",
            "venue",
            BudgetImpact.SAVES,
            "The venue budget can go to food and activities guests remember.",
        ),
        _idea(
            "Activity-based return gifts",
            "Give seed kits, colouring sets or storybooks instead of plastic toys.",
            "return_gifts",
            BudgetImpact.NEUTRAL,
            "Useful gifts last longer and create less plastic waste.",
        ),
        _idea(
            "Cupcake tower",
            "Serve a cupcake tower with a small cutting cake on top.",
            "cake",
            BudgetImpact.SAVES,
            "Cupcakes are easy to portion, waste less and cost less per guest.",
        ),
    ),
)

_COMMUNITY = EventTypeSpec(
    event_type=EventType.COMMUNITY,
    categories=(
        _category("pandal_venue", "Pandal or venue", 22),
        _category("permits", "Permits and fees", 3),
        _category("sound_lighting", "Sound and lighting", 12),
        _category("prasad_catering", "Prasad and catering", 22, scales=True),
        _category("decor", "Decor and flowers", 12),
        _category("puja_rituals", "Puja, idol and rituals", 5),
        _category("cultural_program", "Cultural programme", 10),
        _category("volunteers_logistics", "Volunteers and logistics", 5),
        _category("cleanup", "Cleanup and waste", 4, scales=True),
        _category("contingency", "Contingency", 8),
    ),
    tasks=(
        _task("form_committee", "Form the committee and assign roles", "volunteers_logistics", 60),
        _task("collect_funds", "Collect contributions and sponsorships", None, 60),
        _task("apply_permits", "Apply for police and municipal permissions", "permits", 45),
        _task("book_pandal", "Book the pandal contractor", "pandal_venue", 45),
        _task("loudspeaker_permit", "Apply for loudspeaker permission", "permits", 30),
        _task("book_sound", "Book sound and lighting", "sound_lighting", 30),
        _task("book_caterer", "Arrange the caterer or prasad cooks", "prasad_catering", 21),
        _task("order_decor", "Order decor and flowers", "decor", 14),
        _task("volunteer_roster", "Prepare the volunteer roster", "volunteers_logistics", 14),
        _task("waste_plan", "Arrange waste segregation and a cleanup crew", "cleanup", 14),
        _task("safety_plan", "Plan first aid and crowd safety", "volunteers_logistics", 7),
        _task("sound_check", "Run the sound check", "sound_lighting", 1),
        _task("clean_venue", "Clean up the venue", "cleanup", -1),
        _task("publish_accounts", "Publish the accounts to members", None, -7),
    ),
    vendors=(
        _vendor("pandal_contractor", "Pandal contractor", "pandal_venue", 45),
        _vendor("sound_lighting", "Sound and lighting", "sound_lighting", 30),
        _vendor("decorator", "Decorator", "decor", 21),
        _vendor("caterer", "Caterer", "prasad_catering", 21),
        _vendor("waste_management", "Waste management service", "cleanup", 14),
    ),
    sub_events=(
        SubEventSpec(
            id="puja",
            name="Puja and aarti",
            weight_bonus=(_bonus("puja_rituals", 3), _bonus("decor", 1)),
            tasks=(_task("book_priest", "Book the priest and order the idol", "puja_rituals", 30),),
            vendors=(_vendor("idol_maker", "Idol maker (eco-friendly clay)", "puja_rituals", 45),),
        ),
        SubEventSpec(
            id="cultural_program",
            name="Cultural programme",
            weight_bonus=(_bonus("cultural_program", 4), _bonus("sound_lighting", 3)),
            tasks=(
                _task(
                    "invite_performers",
                    "Invite performers and fix the programme",
                    "cultural_program",
                    21,
                ),
            ),
        ),
        SubEventSpec(
            id="community_meal",
            name="Community meal (bhandara or annadanam)",
            weight_bonus=(_bonus("prasad_catering", 6),),
            tasks=(_task("plan_meal", "Plan the menu and quantities", "prasad_catering", 10),),
        ),
        SubEventSpec(
            id="competitions",
            name="Competitions",
            weight_bonus=(_bonus("cultural_program", 2),),
            tasks=(
                _task(
                    "announce_competitions",
                    "Announce competitions and buy prizes",
                    "cultural_program",
                    14,
                ),
            ),
        ),
        SubEventSpec(
            id="procession",
            name="Procession or visarjan",
            weight_bonus=(
                _bonus("permits", 1),
                _bonus("volunteers_logistics", 2),
                _bonus("sound_lighting", 1),
                _bonus("cleanup", 1),
            ),
            tasks=(
                _task(
                    "procession_permit", "Get route permission for the procession", "permits", 21
                ),
                _task(
                    "procession_volunteers",
                    "Brief the procession volunteers",
                    "volunteers_logistics",
                    3,
                ),
            ),
        ),
    ),
    fallback_ideas=(
        _idea(
            "Eco-friendly clay idol",
            "Choose a clay idol with natural colours for the immersion.",
            "puja_rituals",
            BudgetImpact.NEUTRAL,
            "Clay dissolves safely in water, and many municipalities encourage it.",
        ),
        _idea(
            "Local business sponsorships",
            "Offer stall space and banner spots to local shops in return for sponsorship.",
            None,
            BudgetImpact.SAVES,
            "Sponsorships reduce what the shared fund has to cover.",
        ),
        _idea(
            "Zero-waste prasad",
            "Serve prasad on leaf plates and set up labelled segregation bins.",
            "cleanup",
            BudgetImpact.NEUTRAL,
            "Less mixed waste makes cleanup cheaper and easier for volunteers.",
        ),
    ),
)

CATALOG = MappingProxyType({spec.event_type: spec for spec in (_WEDDING, _BIRTHDAY, _COMMUNITY)})


def spec_for(event_type: EventType) -> EventTypeSpec:
    """Return the catalog entry for an event type."""
    return CATALOG[event_type]


def validate_sub_events(event_type: EventType, sub_events: Sequence[str]) -> tuple[str, ...]:
    """Check selected sub-events and return them once each, in catalog order.

    Args:
        event_type: The event type.
        sub_events: Sub-event ids chosen by the user.

    Returns:
        The selected ids without duplicates, ordered as in the catalog.

    Raises:
        InvalidInputError: If an id is not a sub-event of this event type.
    """
    spec = spec_for(event_type)
    for sub_event_id in sub_events:
        spec.sub_event(sub_event_id)
    chosen = set(sub_events)
    return tuple(s.id for s in spec.sub_events if s.id in chosen)


def tasks_for(event_type: EventType, sub_events: Sequence[str]) -> tuple[TaskSpec, ...]:
    """Return the core tasks plus those of the selected sub-events."""
    spec = spec_for(event_type)
    selected = validate_sub_events(event_type, sub_events)
    extra = [task for s in spec.sub_events if s.id in selected for task in s.tasks]
    return (*spec.tasks, *extra)


def vendors_for(event_type: EventType, sub_events: Sequence[str]) -> tuple[VendorSpec, ...]:
    """Return the core vendors plus those of the selected sub-events."""
    spec = spec_for(event_type)
    selected = validate_sub_events(event_type, sub_events)
    extra = [vendor for s in spec.sub_events if s.id in selected for vendor in s.vendors]
    return (*spec.vendors, *extra)
