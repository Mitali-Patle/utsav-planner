"""Weddings: haldi, mehendi, sangeet, the ceremony and the reception."""

from app.domain.catalog.specs import (
    EventTypeSpec,
    SubEventSpec,
    bonus,
    category,
    idea,
    task,
    vendor,
)
from app.domain.models import BudgetImpact, EventType

WEDDING = EventTypeSpec(
    event_type=EventType.WEDDING,
    categories=(
        category("venue", "Venue and accommodation", 22, scales=True),
        category("catering", "Catering", 25, scales=True),
        category("decor", "Decor and flowers", 12),
        category("photography", "Photography and video", 8),
        category("music", "Music (DJ, band or nadaswaram)", 6),
        category("attire_makeup", "Attire and makeup", 10),
        category("rituals", "Pandit and rituals", 4),
        category("invitations", "Invitations", 3),
        category("gifts", "Gifts and return gifts", 4, scales=True),
        category("transport", "Guest transport", 3),
        category("contingency", "Contingency", 7),
    ),
    tasks=(
        task("fix_muhurtham", "Fix the date and muhurtham with the pandit", "rituals", 180),
        task("book_venue", "Book the venue", "venue", 180),
        task("guest_list", "Finalise the guest list", None, 150),
        task("book_caterer", "Book the caterer and do a menu tasting", "catering", 120),
        task("book_photographer", "Book the photographer and videographer", "photography", 120),
        task("book_decorator", "Book the decorator and agree the theme", "decor", 90),
        task("buy_attire", "Shop for wedding outfits", "attire_makeup", 90),
        task("book_makeup", "Book the makeup artist after a trial", "attire_makeup", 75),
        task("book_music", "Book the DJ, band or nadaswaram", "music", 75),
        task("print_invites", "Order printed and digital invitations", "invitations", 60),
        task("send_invites", "Send invitations", "invitations", 45),
        task("book_transport", "Arrange guest transport and stay", "transport", 45),
        task("buy_gifts", "Buy return gifts", "gifts", 30),
        task("final_headcount", "Give the caterer the final headcount", "catering", 10),
        task("confirm_vendors", "Confirm every vendor and share the schedule", None, 7),
        task("pack_samagri", "Pack the puja samagri for the rituals", "rituals", 3),
        task("prepare_payments", "Prepare vendor payments for the day", None, 1),
        task("settle_accounts", "Settle vendor payments and accounts", None, -7),
    ),
    vendors=(
        vendor("venue", "Venue or banquet hall", "venue", 180),
        vendor("pandit", "Pandit", "rituals", 120),
        vendor("caterer", "Caterer", "catering", 120),
        vendor("photographer", "Photographer and videographer", "photography", 120),
        vendor("decorator", "Decorator", "decor", 90),
        vendor("makeup_artist", "Makeup artist", "attire_makeup", 75),
        vendor("music", "DJ, band or nadaswaram", "music", 75),
        vendor("invitation_printer", "Invitation printer", "invitations", 60),
        vendor("transport", "Transport provider", "transport", 45),
    ),
    sub_events=(
        SubEventSpec(
            id="haldi",
            name="Haldi",
            weight_bonus=(bonus("decor", 2), bonus("rituals", 1)),
            tasks=(task("haldi_setup", "Arrange haldi paste and marigold decor", "decor", 7),),
        ),
        SubEventSpec(
            id="mehendi",
            name="Mehendi",
            weight_bonus=(bonus("attire_makeup", 3), bonus("decor", 1)),
            tasks=(
                task("book_mehendi", "Book the mehendi artist", "attire_makeup", 60),
                task("mehendi_seating", "Plan mehendi seating and music", "decor", 7),
            ),
            vendors=(vendor("mehendi_artist", "Mehendi artist", "attire_makeup", 60),),
        ),
        SubEventSpec(
            id="sangeet",
            name="Sangeet",
            weight_bonus=(bonus("music", 4), bonus("decor", 1)),
            tasks=(
                task(
                    "plan_performances", "Book a choreographer and plan performances", "music", 45
                ),
                task("sangeet_soundcheck", "Run the sangeet sound check", "music", 1),
            ),
            vendors=(vendor("choreographer", "Choreographer", "music", 45),),
        ),
        SubEventSpec(
            id="ceremony",
            name="Wedding ceremony",
            weight_bonus=(bonus("rituals", 2), bonus("decor", 1)),
            tasks=(
                task("book_mandap", "Book the mandap setup", "decor", 60),
                task("confirm_rituals", "Confirm ritual timings with the pandit", "rituals", 7),
            ),
            vendors=(vendor("mandap", "Mandap decorator", "decor", 60),),
        ),
        SubEventSpec(
            id="reception",
            name="Reception",
            weight_bonus=(bonus("catering", 3), bonus("venue", 2), bonus("photography", 1)),
            tasks=(
                task("reception_menu", "Finalise the reception menu", "catering", 14),
                task("reception_stage", "Plan the reception stage and seating", "decor", 21),
            ),
        ),
    ),
    fallback_ideas=(
        idea(
            "Combine haldi and mehendi",
            "Hold haldi in the morning and mehendi in the evening at one venue.",
            "decor",
            BudgetImpact.SAVES,
            "One venue booking, one decor setup and one catering order instead of two.",
        ),
        idea(
            "Seasonal local flowers",
            "Use marigold, rajnigandha and mogra instead of imported flowers.",
            "decor",
            BudgetImpact.SAVES,
            "Local flowers in season are cheaper, fresher and easy to source on the day.",
        ),
        idea(
            "Live counters at the reception",
            "Replace part of the buffet with live chaat and dosa counters.",
            "catering",
            BudgetImpact.NEUTRAL,
            "Food is cooked fresh in smaller batches, so guests enjoy it and less is wasted.",
        ),
    ),
)
