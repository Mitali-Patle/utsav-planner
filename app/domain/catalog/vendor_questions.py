"""Practical questions to ask each kind of vendor before booking."""

from collections.abc import Mapping
from types import MappingProxyType

VENDOR_QUESTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "venue": (
            "Is the quote a hall rental or per plate, and is GST included?",
            "By what time must we vacate, and what does overtime cost?",
            "Can we bring our own caterer and decorator?",
        ),
        "pandit": (
            "What dakshina do you expect, and does it cover the samagri?",
            "Which muhurat do you suggest, and how long will the ceremony take?",
            "What should we arrange for the puja ourselves?",
        ),
        "caterer": (
            "What is the per-plate price for our menu, and is there a minimum guarantee?",
            "Are service staff, crockery and GST included?",
            "Can we have a tasting before we confirm?",
        ),
        "photographer": (
            "How many hours, photographers and videographers are included?",
            "When will we get the edited photos and the album?",
            "How much is the advance, and what happens if the date moves?",
        ),
        "decorator": (
            "Which flowers are fresh and which artificial at this price?",
            "Are setup, removal and transport included?",
            "Can we see photos of a setup you did at our venue?",
        ),
        "makeup_artist": (
            "Is a trial included, and when?",
            "How many people can you get ready, and is draping and hair included?",
            "Do you come to the venue, and is travel charged?",
        ),
        "music": (
            "How many hours are included, and what is the overtime rate?",
            "Is the sound system included, or does the venue provide it?",
            "Can we share a playlist and the songs to avoid?",
        ),
        "invitation_printer": (
            "When can we see a printed proof?",
            "How long does printing take for our quantity?",
            "Can you also make a digital card for WhatsApp?",
        ),
        "transport": (
            "Is the price per day or per kilometre, with fuel and driver allowance?",
            "How many vehicles and seats, and are they air-conditioned?",
            "Who pays tolls and parking?",
        ),
        "entertainer": (
            "How long is the act, and which age group is it for?",
            "What space and equipment do you need?",
            "Is there a backup if you fall ill on the day?",
        ),
        "cake_shop": (
            "Can you make it eggless, and in which flavours?",
            "What is the price per kg for this design?",
            "Do you deliver, and by what time?",
        ),
        "pandal_contractor": (
            "Is the structure fire-safe, and will you help with permissions?",
            "When do you start building, and when do you take it down?",
            "Are lighting and flooring included?",
        ),
        "sound_lighting": (
            "Will the sound stay within the permitted limits and hours?",
            "Do you bring a generator or backup power?",
            "Will a technician stay through the event?",
        ),
        "mandap": (
            "Is the mandap design, flowers and setup included in the price?",
            "How long does setup take, and when can you start at the venue?",
            "Can it be adjusted for the pheras and seating we need?",
        ),
        "mehendi_artist": (
            "Is the price per hand, per side or per person?",
            "Do you use natural henna with no chemicals?",
            "How many artists will come, and for how many hours?",
        ),
        "choreographer": (
            "How many sessions are included, and where will practice happen?",
            "Can you plan steps for people who have never danced?",
            "Will you be there on the day to cue the performers?",
        ),
        "idol_maker": (
            "Is the idol eco-friendly, made of shadu clay with natural colours?",
            "What height and design can we get in our budget?",
            "When can we collect it, and do you help with transport?",
        ),
        "waste_management": (
            "Do you separate wet and dry waste?",
            "How many bins and pickups are included?",
            "Can you take flowers and immersion waste too?",
        ),
    }
)
"""Practical questions to ask each kind of vendor before booking (standard, not AI)."""
