"""Community events: pujas, festivals and society events on a shared fund."""

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

COMMUNITY = EventTypeSpec(
    event_type=EventType.COMMUNITY,
    categories=(
        category("pandal_venue", "Pandal or venue", 22),
        category("permits", "Permits and fees", 3),
        category("sound_lighting", "Sound and lighting", 12),
        category("prasad_catering", "Prasad and catering", 22, scales=True),
        category("decor", "Decor and flowers", 12),
        category("puja_rituals", "Puja, idol and rituals", 5),
        category("cultural_program", "Cultural programme", 10),
        category("volunteers_logistics", "Volunteers and logistics", 5),
        category("cleanup", "Cleanup and waste", 4, scales=True),
        category("contingency", "Contingency", 8),
    ),
    tasks=(
        task("form_committee", "Form the committee and assign roles", "volunteers_logistics", 60),
        task("collect_funds", "Collect contributions and sponsorships", None, 60),
        task("apply_permits", "Apply for police and municipal permissions", "permits", 45),
        task("book_pandal", "Book the pandal contractor", "pandal_venue", 45),
        task("loudspeaker_permit", "Apply for loudspeaker permission", "permits", 30),
        task("book_sound", "Book sound and lighting", "sound_lighting", 30),
        task("book_caterer", "Arrange the caterer or prasad cooks", "prasad_catering", 21),
        task("order_decor", "Order decor and flowers", "decor", 14),
        task("volunteer_roster", "Prepare the volunteer roster", "volunteers_logistics", 14),
        task("waste_plan", "Arrange waste segregation and a cleanup crew", "cleanup", 14),
        task("safety_plan", "Plan first aid and crowd safety", "volunteers_logistics", 7),
        task("sound_check", "Run the sound check", "sound_lighting", 1),
        task("clean_venue", "Clean up the venue", "cleanup", -1),
        task("publish_accounts", "Publish the accounts to members", None, -7),
    ),
    vendors=(
        vendor("pandal_contractor", "Pandal contractor", "pandal_venue", 45),
        vendor("sound_lighting", "Sound and lighting", "sound_lighting", 30),
        vendor("decorator", "Decorator", "decor", 21),
        vendor("caterer", "Caterer", "prasad_catering", 21),
        vendor("waste_management", "Waste management service", "cleanup", 14),
    ),
    sub_events=(
        SubEventSpec(
            id="puja",
            name="Puja and aarti",
            weight_bonus=(bonus("puja_rituals", 3), bonus("decor", 1)),
            tasks=(task("book_priest", "Book the priest and order the idol", "puja_rituals", 30),),
            vendors=(vendor("idol_maker", "Idol maker (eco-friendly clay)", "puja_rituals", 45),),
        ),
        SubEventSpec(
            id="cultural_program",
            name="Cultural programme",
            weight_bonus=(bonus("cultural_program", 4), bonus("sound_lighting", 3)),
            tasks=(
                task(
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
            weight_bonus=(bonus("prasad_catering", 6),),
            tasks=(task("plan_meal", "Plan the menu and quantities", "prasad_catering", 10),),
        ),
        SubEventSpec(
            id="competitions",
            name="Competitions",
            weight_bonus=(bonus("cultural_program", 2),),
            tasks=(
                task(
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
                bonus("permits", 1),
                bonus("volunteers_logistics", 2),
                bonus("sound_lighting", 1),
                bonus("cleanup", 1),
            ),
            tasks=(
                task("procession_permit", "Get route permission for the procession", "permits", 21),
                task(
                    "procession_volunteers",
                    "Brief the procession volunteers",
                    "volunteers_logistics",
                    3,
                ),
            ),
        ),
    ),
    fallback_ideas=(
        idea(
            "Eco-friendly clay idol",
            "Choose a clay idol with natural colours for the immersion.",
            "puja_rituals",
            BudgetImpact.NEUTRAL,
            "Clay dissolves safely in water, and many municipalities encourage it.",
        ),
        idea(
            "Local business sponsorships",
            "Offer stall space and banner spots to local shops in return for sponsorship.",
            None,
            BudgetImpact.SAVES,
            "Sponsorships reduce what the shared fund has to cover.",
        ),
        idea(
            "Zero-waste prasad",
            "Serve prasad on leaf plates and set up labelled segregation bins.",
            "cleanup",
            BudgetImpact.NEUTRAL,
            "Less mixed waste makes cleanup cheaper and easier for volunteers.",
        ),
    ),
)
