"""Birthdays: cake, games and return gifts, at home or at a venue."""

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

BIRTHDAY = EventTypeSpec(
    event_type=EventType.BIRTHDAY,
    categories=(
        category("venue", "Venue", 15, scales=True),
        category("catering", "Food and drinks", 30, scales=True),
        category("cake", "Cake", 10),
        category("decor", "Decorations", 12),
        category("entertainment", "Entertainment and games", 12),
        category("return_gifts", "Return gifts", 8, scales=True),
        category("photography", "Photography", 6),
        category("contingency", "Contingency", 7),
    ),
    tasks=(
        task("guest_list", "Set the budget and guest list", None, 30),
        task("book_venue", "Book the venue or prepare the home space", "venue", 30),
        task("book_entertainer", "Book the entertainer or games host", "entertainment", 21),
        task("send_invites", "Send invitations", None, 21),
        task("plan_theme", "Choose the theme and order decorations", "decor", 14),
        task("book_photographer", "Book a photographer", "photography", 14),
        task("order_cake", "Order the cake", "cake", 10),
        task("buy_return_gifts", "Buy return gifts", "return_gifts", 10),
        task("confirm_rsvps", "Confirm RSVPs and the headcount", None, 3),
        task("confirm_food", "Confirm the food order", "catering", 3),
        task("set_up_decor", "Set up decorations", "decor", 0),
        task("thank_guests", "Pay vendors and thank guests", None, -2),
    ),
    vendors=(
        vendor("venue", "Venue", "venue", 30),
        vendor("caterer", "Caterer or restaurant", "catering", 21),
        vendor("entertainer", "Entertainer", "entertainment", 21),
        vendor("decorator", "Decorator", "decor", 14),
        vendor("photographer", "Photographer", "photography", 14),
        vendor("cake_shop", "Cake shop", "cake", 10),
    ),
    sub_events=(
        SubEventSpec(
            id="puja",
            name="Birthday puja or havan",
            weight_bonus=(bonus("decor", 1), bonus("catering", 1)),
            tasks=(task("book_puja", "Book a pandit for the birthday puja", None, 14),),
        ),
        SubEventSpec(
            id="kids_games",
            name="Kids' games",
            weight_bonus=(bonus("entertainment", 4), bonus("return_gifts", 1)),
            tasks=(task("plan_games", "Plan games and buy prizes", "entertainment", 7),),
        ),
        SubEventSpec(
            id="dinner",
            name="Dinner",
            weight_bonus=(bonus("catering", 4), bonus("venue", 1)),
            tasks=(task("book_dinner", "Book the dinner caterer or restaurant", "catering", 21),),
        ),
    ),
    fallback_ideas=(
        idea(
            "Host at home with a themed corner",
            "Decorate one corner as a photo and cake spot instead of renting a hall.",
            "venue",
            BudgetImpact.SAVES,
            "The venue budget can go to food and activities guests remember.",
        ),
        idea(
            "Activity-based return gifts",
            "Give seed kits, colouring sets or storybooks instead of plastic toys.",
            "return_gifts",
            BudgetImpact.NEUTRAL,
            "Useful gifts last longer and create less plastic waste.",
        ),
        idea(
            "Cupcake tower",
            "Serve a cupcake tower with a small cutting cake on top.",
            "cake",
            BudgetImpact.SAVES,
            "Cupcakes are easy to portion, waste less and cost less per guest.",
        ),
    ),
)
