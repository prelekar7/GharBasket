"""notifications: app copy (en/hi/mr) for the messages M11 now sends for points_credited,
donation_verified, society_live, route_published (rider and captain, box and tiffin) and
assignment_created (M11 §3.1 event map)

Revision ID: 7e4b9d20c6a1
Revises: 2a9bfe1bd02f
Create Date: 2026-11-01 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e4b9d20c6a1"
down_revision: str | Sequence[str] | None = "2a9bfe1bd02f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (kind, en, hi, mr), each (title, body), as in 20261010_1415. Reviewed by Rhythm Thakur and
# Manish Raje before release (M11 §9); edits go through PUT /v1/admin/notification-templates.
# Marathi clock times come without वाजता (render.clock), so the texts add it where it reads.
COPY: list[tuple[str, tuple[str, str], tuple[str, str], tuple[str, str]]] = [
    (
        "billing.points_credited",
        (
            "You earned {points} Kindness points",
            "They are in your Kindness balance. Use them to donate a meal.",
        ),
        (
            "आपने {points} काइंडनेस पॉइंट कमाए",
            "वे आपके काइंडनेस बैलेंस में हैं। उनसे एक भोजन दान करें।",
        ),
        (
            "तुम्ही {points} काइंडनेस पॉइंट्स मिळवले",
            "ते तुमच्या काइंडनेस शिलकीत आहेत. त्यातून एक जेवण दान करा.",
        ),
    ),
    (
        "community.donation_verified",
        (
            "Your donation was verified",
            "Thank you. {points} Kindness points have been added.",
        ),
        (
            "आपका दान सत्यापित हो गया",
            "धन्यवाद। {points} काइंडनेस पॉइंट जुड़ गए हैं।",
        ),
        (
            "तुमचे दान पडताळले गेले",
            "धन्यवाद. {points} काइंडनेस पॉइंट्स जोडले गेले आहेत.",
        ),
    ),
    (
        "community.society_live",
        (
            "Your society is live",
            "Delivery is now free for everyone here.",
        ),
        (
            "आपकी सोसाइटी अब लाइव है",
            "अब यहाँ सभी के लिए डिलीवरी मुफ़्त है।",
        ),
        (
            "तुमची सोसायटी आता लाइव्ह आहे",
            "आता इथे सर्वांसाठी डिलिव्हरी मोफत आहे.",
        ),
    ),
    (
        "logistics.route_published.rider",
        (
            "Your {day} route is ready",
            "{stops} stops. Leave at {depart}.",
        ),
        (
            "{day} का आपका रूट तैयार है",
            "{stops} स्टॉप। {depart} निकलें।",
        ),
        (
            "{day} चा तुमचा रूट तयार आहे",
            "{stops} थांबे. {depart} वाजता निघा.",
        ),
    ),
    (
        "logistics.route_published.rider_tiffin",
        (
            "Your tiffin run on {day}",
            "{kitchens} kitchens, {societies} societies. First pickup at {first_pickup}.",
        ),
        (
            "{day} की आपकी टिफ़िन डिलीवरी",
            "{kitchens} किचन, {societies} सोसाइटी। पहला पिकअप {first_pickup}।",
        ),
        (
            "{day} ची तुमची टिफिन फेरी",
            "{kitchens} किचन, {societies} सोसायटी. पहिला पिकअप {first_pickup} वाजता.",
        ),
    ),
    (
        "logistics.route_published.captain",
        (
            "Your society's bags on {day}",
            "{bags} in all. The van is expected at about {eta}.",
        ),
        (
            "{day} को आपकी सोसाइटी के बैग",
            "कुल {bags}। वैन लगभग {eta} पहुँचेगी।",
        ),
        (
            "{day} ला तुमच्या सोसायटीच्या बॅगा",
            "एकूण {bags}. व्हॅन साधारण {eta} वाजता पोहोचेल.",
        ),
    ),
    (
        "logistics.route_published.captain_tiffin",
        (
            "Your society's tiffins on {day}",
            "{bags} in all. The rider is expected at about {eta}.",
        ),
        (
            "{day} को आपकी सोसाइटी के टिफ़िन",
            "कुल {bags}। राइडर लगभग {eta} पहुँचेगा।",
        ),
        (
            "{day} ला तुमच्या सोसायटीचे टिफिन",
            "एकूण {bags}. रायडर साधारण {eta} वाजता पोहोचेल.",
        ),
    ),
    (
        "partners.assignment_created",
        (
            "New tiffin order for {day}",
            "{portions} portions for {meal}. Reply before {respond_by}.",
        ),
        (
            "{day} के लिए नया टिफ़िन ऑर्डर",
            "{meal}: {portions} पोर्शन। {respond_by} से पहले जवाब दें।",
        ),
        (
            "{day} साठी नवीन टिफिन ऑर्डर",
            "{meal}: {portions} पोर्शन. {respond_by} पर्यंत उत्तर द्या.",
        ),
    ),
]

templates = sa.table(
    "templates",
    sa.column("kind", sa.String),
    sa.column("channel", sa.String),
    sa.column("locale", sa.String),
    sa.column("version", sa.SmallInteger),
    sa.column("title", sa.String),
    sa.column("body", sa.String),
    sa.column("active", sa.Boolean),
    schema="notifications",
)


def upgrade() -> None:
    op.bulk_insert(
        templates,
        [
            {
                "kind": kind,
                "channel": "app",
                "locale": locale,
                "version": 1,
                "title": title,
                "body": body,
                "active": True,
            }
            for kind, *texts in COPY
            for locale, (title, body) in zip(("en-IN", "hi-IN", "mr-IN"), texts, strict=True)
        ],
    )


def downgrade() -> None:
    # Every version of these kinds, including ones saved later through the console.
    op.execute(templates.delete().where(templates.c.kind.in_([kind for kind, *_ in COPY])))
