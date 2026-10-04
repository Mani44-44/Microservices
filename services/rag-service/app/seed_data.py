"""~15-20 short seed documents: half travel-planning knowledge, half
support-policy knowledge. Loaded once on startup if the documents table is
empty (see app/main.py lifespan)."""

TRAVEL_DOCS = [
    ("Most countries in the Schengen Area allow visa-free entry for US, UK, and "
     "Canadian passport holders for stays up to 90 days. Always check the latest "
     "visa requirements before booking international travel.", "visa_guide"),
    ("Travelers to Japan from most Western countries do not need a visa for tourist "
     "stays under 90 days. A valid passport with at least six months' validity is "
     "required.", "visa_guide"),
    ("Pack light and use packing cubes to organize clothing by category. Roll "
     "clothes instead of folding to save space and reduce wrinkles.", "packing_tips"),
    ("Always carry a portable charger, universal power adapter, and a copy of "
     "important documents when traveling internationally.", "packing_tips"),
    ("Paris is one of the most visited cities in the world, known for the Eiffel "
     "Tower, the Louvre, and its café culture. Spring and fall are the best times "
     "to visit for mild weather.", "destinations"),
    ("Tokyo offers a mix of ultramodern and traditional experiences, from the "
     "Shibuya crossing to historic temples in Asakusa. Cherry blossom season in "
     "late March to early April is extremely popular.", "destinations"),
    ("Bali is a popular destination for beach relaxation, surfing, and yoga "
     "retreats. The dry season from May to September offers the best weather.", "destinations"),
    ("New York City sees hot, humid summers and cold winters. The most pleasant "
     "weather for sightseeing is typically in spring (April-May) and fall "
     "(September-October).", "weather_patterns"),
    ("Southeast Asia has a monsoon season roughly from June to October, with "
     "heavy rainfall in many regions. Traveling in the dry season (November to "
     "April) is generally recommended.", "weather_patterns"),
    ("Music festivals typically run over a weekend and often require advance "
     "ticket purchase, while local street fairs are usually free and open to "
     "walk-ins.", "event_types"),
    ("Sporting events such as championship finals often sell out months in "
     "advance, so early ticket purchase is strongly recommended.", "event_types"),
]

SUPPORT_DOCS = [
    ("Tickets are fully refundable if canceled more than 7 days before the event "
     "start date. Cancellations within 7 days are eligible for a 50% refund.", "refund_policy"),
    ("No refunds are issued for cancellations made within 24 hours of the event "
     "start time, except in cases where the event itself is canceled by the "
     "organizer.", "refund_policy"),
    ("If an event is canceled or postponed by the organizer, all ticket holders "
     "are entitled to a full refund regardless of when they purchased their "
     "ticket.", "refund_policy"),
    ("Refunds are processed to the original payment method and typically appear "
     "within 5-10 business days after approval.", "refund_policy"),
    ("Tickets can be canceled from the 'My Bookings' page up until the "
     "cancellation window closes for that event. After that, cancellation "
     "requires contacting support.", "cancellation_policy"),
    ("Group bookings of 10 or more tickets may be subject to a different "
     "cancellation policy; contact support for group-specific terms.", "cancellation_policy"),
    ("Tickets can be transferred to another person's account up to 48 hours "
     "before the event start time via the 'Transfer Ticket' option on the "
     "booking details page.", "ticket_transfer"),
    ("Transferred tickets cannot be transferred a second time. The new ticket "
     "holder becomes the sole owner of record for entry and refund purposes.", "ticket_transfer"),
    ("There is no fee for transferring a ticket to another user within the same "
     "event.", "ticket_transfer"),
]


def _as_docs(pairs, category):
    return [
        {"content": content, "category": category, "source": source, "metadata": {}}
        for content, source in pairs
    ]


SEED_DOCUMENTS = _as_docs(TRAVEL_DOCS, "travel") + _as_docs(SUPPORT_DOCS, "support")
