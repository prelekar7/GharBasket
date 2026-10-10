"""notifications: the first app copy (en/hi/mr) for the partner messages, partners.verified per
partner kind and community.captain_assigned (M11 §3.1 event map, M8 §10, M9 §3.2)

Revision ID: c3d7e1a95b42
Revises: 232e527f6a85
Create Date: 2026-10-10 14:15:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d7e1a95b42"
down_revision: str | Sequence[str] | None = "232e527f6a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (kind, en, hi, mr), each (title, body), as in 20261008_1232. Reviewed by Rhythm Thakur and
# Manish Raje before release (M11 §9); edits go through PUT /v1/admin/notification-templates.
COPY: list[tuple[str, tuple[str, str], tuple[str, str], tuple[str, str]]] = [
    (
        "partners.verified.kitchen",
        (
            "Your kitchen is verified on GharBasket",
            "Set your capacity and menus to start getting tiffin orders.",
        ),
        (
            "आपका किचन GharBasket पर सत्यापित हो गया",
            "टिफ़िन ऑर्डर पाने के लिए अपनी क्षमता और मेन्यू तय करें।",
        ),
        (
            "तुमचे किचन GharBasket वर पडताळले गेले",
            "टिफिन ऑर्डर मिळवण्यासाठी तुमची क्षमता आणि मेन्यू ठरवा.",
        ),
    ),
    (
        "partners.verified.captain",
        (
            "You're a verified Society Captain",
            "We'll tell you as soon as your society is yours to serve.",
        ),
        (
            "आप सत्यापित सोसाइटी कैप्टन हैं",
            "आपकी सोसाइटी आपको सौंपते ही हम आपको बताएँगे।",
        ),
        (
            "तुम्ही पडताळलेल्या सोसायटी कॅप्टन आहात",
            "तुमची सोसायटी तुमच्याकडे सोपवताच आम्ही तुम्हाला कळवू.",
        ),
    ),
    (
        "partners.verified.rider",
        (
            "You're a verified GharBasket rider",
            "Your routes show up here once the hub plans them.",
        ),
        (
            "आप GharBasket के सत्यापित राइडर हैं",
            "हब के प्लान करते ही आपके रूट यहाँ दिखेंगे।",
        ),
        (
            "तुम्ही GharBasket चे पडताळलेले रायडर आहात",
            "हबने नियोजन केल्यावर तुमचे रूट इथे दिसतील.",
        ),
    ),
    (
        "partners.verified.packer",
        (
            "You're a verified GharBasket packer",
            "Your packing lists show up here on delivery days.",
        ),
        (
            "आप GharBasket के सत्यापित पैकर हैं",
            "डिलीवरी के दिनों में आपकी पैकिंग सूचियाँ यहाँ दिखेंगी।",
        ),
        (
            "तुम्ही GharBasket चे पडताळलेले पॅकर आहात",
            "डिलिव्हरीच्या दिवशी तुमच्या पॅकिंगच्या याद्या इथे दिसतील.",
        ),
    ),
    (
        "community.captain_assigned",
        (
            "You're now your society's Captain",
            "Your society's bags come to you. See the day's list under Captain.",
        ),
        (
            "अब आप अपनी सोसाइटी की कैप्टन हैं",
            "आपकी सोसाइटी के बैग आपके पास आएँगे। दिन की सूची कैप्टन में देखें।",
        ),
        (
            "आता तुम्ही तुमच्या सोसायटीच्या कॅप्टन आहात",
            "तुमच्या सोसायटीच्या बॅगा तुमच्याकडे येतील. दिवसाची यादी कॅप्टनमध्ये पाहा.",
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
