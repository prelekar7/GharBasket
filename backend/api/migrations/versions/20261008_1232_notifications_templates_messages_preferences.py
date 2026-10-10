"""notifications: templates with the first copy in en/hi/mr, inbox messages, preferences and
quiet hours (M11 §3.1, §4)

Revision ID: 4f1c2a7d9e30
Revises: a8e9dd07f72c
Create Date: 2026-10-08 12:32:29.499543+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4f1c2a7d9e30"
down_revision: str | Sequence[str] | None = "a8e9dd07f72c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The first app copy (inbox and push) per kind: (kind, en, hi, mr), each (title, body).
# Reviewed by Rhythm Thakur and Manish Raje before release (M11 §9); edits later go through
# PUT /v1/admin/notification-templates, which makes a new version.
COPY: list[tuple[str, tuple[str, str], tuple[str, str], tuple[str, str]]] = [
    (
        "subscriptions.box_ready",
        (
            "Your week is ready to check",
            "{total} box for the week. Approve or change it by {cutoff_day} {cutoff_time}.",
        ),
        (
            "आपका हफ़्ता देखने के लिए तैयार",
            "इस हफ़्ते का बॉक्स {total} का है। {cutoff_day} {cutoff_time} तक मंज़ूर करें या बदलें।",
        ),
        (
            "तुमचा आठवडा पाहण्यासाठी तयार",
            "या आठवड्याचा बॉक्स {total} चा आहे. {cutoff_day} {cutoff_time} पर्यंत मंजूर करा किंवा बदला.",
        ),
    ),
    (
        "subscriptions.cutoff_reminder",
        ("Edits close at {cutoff_time}", "Check this week's box and approve it, or skip."),
        ("बदलाव {cutoff_time} बंद", "इस हफ़्ते का बॉक्स देखकर मंज़ूर करें, या स्किप करें।"),
        ("बदल {cutoff_time} पर्यंतच", "या आठवड्याचा बॉक्स पाहून मंजूर करा, किंवा स्किप करा."),
    ),
    (
        "subscriptions.box_approved",
        ("We approved your box", "Autopilot approved your box for {day}, as you asked."),
        ("आपका बॉक्स मंज़ूर हो गया", "आपके कहे अनुसार ऑटोपायलट ने {day} का बॉक्स मंज़ूर कर दिया।"),
        ("तुमचा बॉक्स मंजूर झाला", "तुमच्या सांगण्यानुसार ऑटोपायलटने {day} चा बॉक्स मंजूर केला."),
    ),
    (
        "orders.locked",
        ("Your box for {day} is confirmed", "{total} was paid from your wallet."),
        ("{day} का बॉक्स पक्का", "आपके वॉलेट से {total} चुकाए गए।"),
        ("{day} चा बॉक्स निश्चित", "तुमच्या वॉलेटमधून {total} भरले."),
    ),
    (
        "orders.payment_failed",
        (
            "Top up to get this week's box",
            "Your wallet is {shortfall} short. Add money by {grace_time} or the box is cancelled.",
        ),
        (
            "इस हफ़्ते का बॉक्स पाने के लिए पैसे जोड़ें",
            "आपके वॉलेट में {shortfall} कम हैं। {grace_time} तक पैसे जोड़ें, नहीं तो बॉक्स रद्द हो जाएगा।",
        ),
        (
            "या आठवड्याच्या बॉक्ससाठी पैसे भरा",
            "तुमच्या वॉलेटमध्ये {shortfall} कमी आहेत. {grace_time} पर्यंत पैसे भरा, नाहीतर बॉक्स रद्द होईल.",
        ),
    ),
    (
        "orders.cancelled",
        ("Your box for {day} is cancelled", "The wallet was short in time. Nothing was charged."),
        ("{day} का बॉक्स रद्द", "समय पर वॉलेट में पूरे पैसे नहीं थे। कोई पैसा नहीं कटा।"),
        ("{day} चा बॉक्स रद्द", "वेळेत वॉलेटमध्ये पुरेसे पैसे नव्हते. काहीही कापले गेले नाही."),
    ),
    (
        "orders.dispatched",
        ("Your box is on its way", "It has left the hub and reaches your society soon."),
        ("आपका बॉक्स रास्ते में है", "यह हब से निकल चुका है और जल्द आपकी सोसाइटी पहुँचेगा।"),
        ("तुमचा बॉक्स निघाला आहे", "तो हबमधून निघाला आहे आणि लवकरच तुमच्या सोसायटीत पोहोचेल."),
    ),
    (
        "orders.at_society",
        ("Your box has reached your society", "Your Society Captain brings it to your door soon."),
        ("आपका बॉक्स सोसाइटी पहुँच गया", "आपकी सोसाइटी कैप्टन जल्द इसे आपके दरवाज़े तक लाएँगी।"),
        ("तुमचा बॉक्स सोसायटीत पोहोचला", "तुमच्या सोसायटी कॅप्टन लवकरच तो तुमच्या दारापर्यंत आणतील."),
    ),
    (
        "orders.delivered",
        ("Your box is delivered", "Something missing? Tell us from the order."),
        ("आपका बॉक्स पहुँच गया", "कुछ कम है? ऑर्डर से हमें बताएँ।"),
        ("तुमचा बॉक्स पोहोचला", "काही कमी आहे? ऑर्डरमधून आम्हाला कळवा."),
    ),
    (
        "orders.delivery_failed",
        ("We couldn't deliver your box", "Open the order to see what happens next."),
        ("हम आपका बॉक्स नहीं पहुँचा सके", "आगे क्या होगा, यह ऑर्डर में देखें।"),
        ("आम्ही तुमचा बॉक्स पोहोचवू शकलो नाही", "पुढे काय होईल ते ऑर्डरमध्ये पाहा."),
    ),
    (
        "orders.shortage_substituted",
        (
            "One item in your box changed",
            "An item ran short, so we packed a substitute. {refund} is back in your wallet.",
        ),
        (
            "आपके बॉक्स में एक चीज़ बदली",
            "एक चीज़ कम पड़ गई, इसलिए हमने उसकी जगह दूसरी रखी। {refund} आपके वॉलेट में वापस आ गए।",
        ),
        (
            "तुमच्या बॉक्समधली एक वस्तू बदलली",
            "एक वस्तू कमी पडली, म्हणून आम्ही तिच्या जागी दुसरी ठेवली. {refund} तुमच्या वॉलेटमध्ये परत आले.",
        ),
    ),
    (
        "orders.issue_resolved.credited",
        ("{credit} credited for your report", "Thanks for telling us. It is in your wallet."),
        ("आपकी शिकायत पर {credit} जमा", "बताने के लिए धन्यवाद। पैसे आपके वॉलेट में हैं।"),
        ("तुमच्या तक्रारीसाठी {credit} जमा", "कळवल्याबद्दल धन्यवाद. पैसे तुमच्या वॉलेटमध्ये आहेत."),
    ),
    (
        "orders.issue_resolved.rejected",
        ("About your report", "We checked it and couldn't credit it this time. See the order."),
        ("आपकी शिकायत के बारे में", "हमने जाँच की, पर इस बार पैसे नहीं लौटा सके। ऑर्डर में देखें।"),
        ("तुमच्या तक्रारीबद्दल", "आम्ही तपासले, पण या वेळी पैसे परत करता आले नाहीत. ऑर्डरमध्ये पाहा."),
    ),
    (
        "billing.wallet_topped_up",
        ("{amount} added to your wallet", "It's ready for your next box."),
        ("आपके वॉलेट में {amount} जुड़े", "यह आपके अगले बॉक्स के लिए तैयार है।"),
        ("तुमच्या वॉलेटमध्ये {amount} जमा झाले", "पुढच्या बॉक्ससाठी ते तयार आहेत."),
    ),
    (
        "identity.data_export_ready",
        ("Your data is ready", "Download it from Profile within 7 days."),
        ("आपका डेटा तैयार है", "इसे 7 दिनों के अंदर प्रोफ़ाइल से डाउनलोड करें।"),
        ("तुमचा डेटा तयार आहे", "तो 7 दिवसांच्या आत प्रोफाइलमधून डाउनलोड करा."),
    ),
]


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=12), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('weekly_box', 'delivery', 'money', 'kindness', 'offers', 'account')",
            name=op.f("ck_messages_category"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("event_id", "user_id", name=op.f("uq_messages_event_id_user_id")),
        schema="notifications",
    )
    op.create_index(
        "ix_messages_unread",
        "messages",
        ["user_id"],
        unique=False,
        schema="notifications",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index(
        "ix_messages_user", "messages", ["user_id", "id"], unique=False, schema="notifications"
    )
    op.create_table(
        "preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=12), nullable=False),
        sa.Column("push", sa.Boolean(), nullable=False),
        sa.Column("whatsapp", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "category IN ('weekly_box', 'delivery', 'money', 'kindness', 'offers')",
            name=op.f("ck_preferences_category"),
        ),
        sa.PrimaryKeyConstraint("user_id", "category", name=op.f("pk_preferences")),
        schema="notifications",
    )
    op.create_table(
        "quiet_hours",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.CheckConstraint("start_time <> end_time", name=op.f("ck_quiet_hours_not_empty")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_quiet_hours")),
        schema="notifications",
    )
    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("locale", sa.String(length=5), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("wa_template_name", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("channel IN ('app', 'whatsapp')", name=op.f("ck_templates_channel")),
        sa.CheckConstraint(
            "locale IN ('en-IN', 'hi-IN', 'mr-IN')", name=op.f("ck_templates_locale")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_templates")),
        sa.UniqueConstraint(
            "kind",
            "channel",
            "locale",
            "version",
            name=op.f("uq_templates_kind_channel_locale_version"),
        ),
        schema="notifications",
    )
    op.create_index(
        "ux_templates_active",
        "templates",
        ["kind", "channel", "locale"],
        unique=True,
        schema="notifications",
        postgresql_where=sa.text("active"),
    )
    # ### end Alembic commands ###
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
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        "ux_templates_active",
        table_name="templates",
        schema="notifications",
        postgresql_where=sa.text("active"),
    )
    op.drop_table("templates", schema="notifications")
    op.drop_table("quiet_hours", schema="notifications")
    op.drop_table("preferences", schema="notifications")
    op.drop_index("ix_messages_user", table_name="messages", schema="notifications")
    op.drop_index(
        "ix_messages_unread",
        table_name="messages",
        schema="notifications",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_table("messages", schema="notifications")
    # ### end Alembic commands ###
