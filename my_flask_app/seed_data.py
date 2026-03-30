"""Seed data used to populate a fresh Bristol Community Events database."""

DEFAULT_REVIEWS = [
    {
        "seed_key": "demo-john-smith",
        "author_name": "John Smith",
        "author_initials": "JS",
        "rating": 5,
        "content": "I checked what was on around the harbourside and found a couple of events I'd actually go to.",
        "status": "Approved",
    },
    {
        "seed_key": "demo-emily-carter",
        "author_name": "Emily Carter",
        "author_initials": "EC",
        "rating": 4,
        "content": "The filters make it easy to narrow things down by date and price without jumping between pages.",
        "status": "Approved",
    },
    {
        "seed_key": "demo-david-brown",
        "author_name": "David Brown",
        "author_initials": "DB",
        "rating": 5,
        "content": "Booking was straightforward, and the receipt was ready straight away.",
        "status": "Approved",
    },
    {
        "seed_key": "demo-aisha-rahman",
        "author_name": "Aisha Rahman",
        "author_initials": "AR",
        "rating": 4,
        "content": "I like seeing the venue, location, and capacity together before I decide.",
        "status": "Approved",
    },
    {
        "seed_key": "demo-sofia-patel",
        "author_name": "Sofia Patel",
        "author_initials": "SP",
        "rating": 5,
        "content": "The student discount is useful, especially for pricier events.",
        "status": "Approved",
    },
    {
        "seed_key": "demo-daniel-green",
        "author_name": "Daniel Green",
        "author_initials": "DG",
        "rating": 4,
        "content": "It feels like a practical local events site rather than a generic ticket page.",
        "status": "Approved",
    },
]

DEFAULT_VENUES = [
    {
        "venue_name": "Bristol City Centre Hall",
        "address": "Broad Street",
        "city": "Bristol",
        "capacity": 500,
    },
    {
        "venue_name": "Harbourside Gallery",
        "address": "Dock Road",
        "city": "Bristol",
        "capacity": 300,
    },
    {
        "venue_name": "Ashton Court Estate",
        "address": "Ashton Court",
        "city": "Bristol",
        "capacity": 400,
    },
    {
        "venue_name": "Bristol Indoor Arena",
        "address": "Arena Road",
        "city": "Bristol",
        "capacity": 500,
    },
    {
        "venue_name": "Harbourside Art Space",
        "address": "Dock Street",
        "city": "Bristol",
        "capacity": 300,
    },
    {
        "venue_name": "UWE Exhibition Hall",
        "address": "Frenchay Campus",
        "city": "Bristol",
        "capacity": 400,
    },
]
