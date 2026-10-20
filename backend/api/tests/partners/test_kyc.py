"""KYC documents (M8 §3.1): no Aadhaar, the formats, the simulated checks, encryption with masked
numbers only, private files claimed from presign, superseding, and audited file links for that
hub's staff only."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners.models import KycDocument
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.models import AuditEntry, OutboxEvent, Upload

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..test_files import SETTINGS as STORAGE_SETTINGS
from ..test_files import FakeS3
from .conftest import NEHA, PRIYA, RAMESH, SUNITA, VIKRAM, as_user, other_hub_manager
from .test_applications import HUB_URL, apply, code, post

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

SETTINGS = make_settings()
FUTURE = (date.today() + timedelta(days=400)).isoformat()


def store_for(s3: FakeS3) -> ObjectStore:
    store = create_store(STORAGE_SETTINGS)
    assert store is not None
    store.client = s3
    return store


async def upload_key(
    db: AsyncSession, s3: FakeS3, user_id: uuid.UUID, *, purpose: str = "kyc_document"
) -> str:
    """What POST /v1/files/presign and the app's PUT leave behind."""
    key = f"{purpose}/{user_id}/{uuid.uuid4()}.jpg"
    db.add(
        Upload(key=key, user_id=user_id, purpose=purpose, content_type="image/jpeg", size_bytes=9)
    )
    s3.objects[key] = ("image/jpeg", 9)
    await db.flush()
    return key


async def send(
    api: httpx2.AsyncClient, auth: dict[str, str], application_id: str, **body: Any
) -> httpx2.Response:
    body.setdefault("file_keys", [])
    return await post(api, "/v1/kyc-documents", auth, {"application_id": application_id, **body})


VOTER_ID = {"doc_type": "id_proof", "id_kind": "voter_id", "number": "abc-123 4567"}
BANK = {
    "doc_type": "bank",
    "number": "1234 5678 9012",
    "ifsc": "sbin0001234",
    "account_holder": " Priya  Sharma ",
}


async def test_documents_are_encrypted_masked_and_move_the_application_to_review(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = clients(storage=store_for(s3))
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    application = (await apply(api, priya, kind="packer")).json()
    key = await upload_key(db_session, s3, priya_id)

    voter = await send(api, priya, application["id"], **VOTER_ID, file_keys=[key])
    halfway = await api.get("/v1/partner-applications/me", headers=priya)
    bank = await post(
        api, "/v1/kyc-documents", priya, {"application_id": application["id"], **BANK}, "k-bank"
    )
    replay = await post(
        api, "/v1/kyc-documents", priya, {"application_id": application["id"], **BANK}, "k-bank"
    )
    mine = await api.get(f"/v1/partner-applications/{application['id']}", headers=priya)
    queue = await api.get(HUB_URL, headers=vikram)

    assert voter.status_code == 201, voter.text
    body = voter.json()
    assert (body["doc_type"], body["id_kind"], body["number_masked"]) == (
        "id_proof",
        "voter_id",
        "XXXXXX4567",
    )
    assert body["fssai_reg_no"] is None and body["details"] == {} and body["file_count"] == 1
    assert (body["check"]["status"], body["check"]["code"]) == ("passed", "format_ok")
    assert body["review"] == {"status": "pending", "reason": None, "at": None}
    assert halfway.json()["status"] == "applied"
    assert halfway.json()["requirements"]["documents"][0] == {
        "doc_type": "id_proof",
        "status": "submitted",
        "document_id": body["id"],
        "reason": None,
    }
    assert bank.status_code == 201, bank.text
    assert bank.json()["number_masked"] == "XXXXXXXX9012"
    assert bank.json()["details"] == {"ifsc": "SBIN0001234", "account_holder": "Priya Sharma"}
    assert (bank.json()["check"]["status"], bank.json()["check"]["code"]) == (
        "passed",
        "penny_drop_ok",
    )
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == bank.json()["id"]
    assert mine.json()["status"] == "kyc_pending" and mine.json()["next_step"] == "review"
    assert [d["doc_type"] for d in mine.json()["documents"]] == ["id_proof", "bank"]
    assert [a["id"] for a in queue.json()["items"]] == [application["id"]]
    assert [d["doc_type"] for d in queue.json()["items"][0]["documents"]] == ["id_proof", "bank"]

    # Stored only encrypted, masked and hashed; it decrypts with the key, and only with it.
    row = await db_session.get(KycDocument, uuid.UUID(body["id"]))
    assert row is not None and row.key_version == 1
    assert row.number_hash is not None and len(row.number_hash) == 32
    assert b"ABC1234567" not in (row.number_enc or b"")
    decrypt = text("SELECT pgp_sym_decrypt(:enc, :key)")
    clear = await db_session.scalar(decrypt, {"enc": row.number_enc, "key": SETTINGS.kyc_key})
    assert clear == "ABC1234567"
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.scalar(decrypt, {"enc": row.number_enc, "key": "not-the-key"})
    claimed = await db_session.get(Upload, key)
    assert claimed is not None and claimed.claimed_by == "partners"

    # No answer and no event carries a number in clear.
    events = await db_session.execute(select(OutboxEvent.payload))
    seen = " ".join(
        [voter.text, bank.text, mine.text, queue.text, halfway.text]
        + [json.dumps(payload) for payload in events.scalars()]
    )
    assert "ABC1234567" not in seen and "123456789012" not in seen


async def test_aadhaar_is_refused_before_anything_is_stored(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    application = (await apply(api, priya, kind="packer")).json()
    await db_session.commit()

    by_kind = await send(api, priya, application["id"], doc_type="id_proof", id_kind="Aadhaar")
    by_number = await send(
        api,
        priya,
        application["id"],
        doc_type="id_proof",
        id_kind="voter_id",
        number="1234 5678 9012",
    )

    assert code(by_kind) == code(by_number) == (422, "partners.aadhaar_not_accepted")
    assert await db_session.scalar(select(func.count()).select_from(KycDocument)) == 0


async def test_each_rule_on_types_numbers_dates_and_files(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = clients(storage=store_for(s3))
    priya, priya_id = await as_user(api, google, PRIYA)
    sunita, _ = await as_user(api, google, SUNITA)
    ramesh, _ = await as_user(api, google, RAMESH)
    kitchen = (await apply(api, priya, kind="kitchen")).json()["id"]
    rider = (await apply(api, sunita, kind="rider")).json()["id"]
    captain = (await apply(api, ramesh, kind="captain")).json()["id"]
    photo = await upload_key(db_session, s3, priya_id)
    await db_session.commit()
    licence = {
        "doc_type": "driving_licence",
        "number": "MH46 2019 0001234",
        "valid_until": FUTURE,
        "vehicle_no": "MH 46 AB 1234",
    }
    fssai = {"doc_type": "fssai", "number": "11521999000123", "valid_until": FUTURE}
    cases: list[tuple[dict[str, str], str, dict[str, Any], str, str | None]] = [
        (priya, kitchen, {"doc_type": "pan", "number": "ABCD1234F"}, "number_invalid", "number"),
        (priya, kitchen, {**VOTER_ID, "number": "AB1234567"}, "number_invalid", "number"),
        (
            priya,
            kitchen,
            {"doc_type": "id_proof", "id_kind": "passport", "number": "12345678"},
            "number_invalid",
            "number",
        ),
        (priya, kitchen, {"doc_type": "id_proof", "number": "A1234567"}, "doc_type_invalid", None),
        (priya, kitchen, {**BANK, "number": "12345678"}, "number_invalid", "number"),
        (priya, kitchen, {**BANK, "ifsc": "SBIN1001234"}, "number_invalid", "ifsc"),
        (priya, kitchen, {**BANK, "account_holder": None}, "number_invalid", "account_holder"),
        (priya, kitchen, {**fssai, "number": "1152199900012"}, "number_invalid", "number"),
        (priya, kitchen, {**fssai, "valid_until": "2020-01-01"}, "document_expired", None),
        (
            priya,
            kitchen,
            {"doc_type": "kitchen_photos", "file_keys": [photo]},
            "file_key_invalid",
            None,
        ),
        (priya, kitchen, licence, "doc_type_invalid", None),  # a kitchen needs no licence
        (sunita, rider, {**licence, "number": "MH46201900"}, "number_invalid", "number"),
        (sunita, rider, {**licence, "vehicle_no": "XYZ"}, "number_invalid", "vehicle_no"),
        (sunita, rider, {**licence, "valid_until": None}, "number_invalid", "valid_until"),
        (ramesh, captain, {"doc_type": "society_proof"}, "doc_type_invalid", None),
        (ramesh, captain, fssai, "doc_type_invalid", None),
    ]
    for auth, application_id, body, expected, field in cases:
        answer = await send(api, auth, application_id, **body)
        assert code(answer) == (422, f"partners.{expected}"), (body, answer.text)
        assert answer.json().get("field") == field, body

    ok_licence = await send(api, sunita, rider, **licence)
    ok_fssai = await send(api, priya, kitchen, **fssai)
    assert ok_licence.status_code == 201, ok_licence.text
    assert ok_licence.json()["number_masked"] == "XXXXXXXXXXX1234"
    assert ok_licence.json()["details"] == {"valid_until": FUTURE, "vehicle_no": "MH46AB1234"}
    # The FSSAI number is public by law: in full, and not encrypted.
    assert ok_fssai.json()["fssai_reg_no"] == "11521999000123"
    assert ok_fssai.json()["number_masked"] is None


async def test_the_penny_drop_hook_and_duplicates_across_people(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    sunita, _ = await as_user(api, google, SUNITA)
    ramesh, _ = await as_user(api, google, RAMESH)
    mine = (await apply(api, priya, kind="packer")).json()["id"]
    hers = (await apply(api, sunita, kind="packer")).json()["id"]

    failed = await send(api, priya, mine, **{**BANK, "number": "55551110000"})
    first = await send(api, priya, mine, **BANK)
    again_mine = await send(api, priya, mine, **BANK)  # her own replacement is no duplicate
    same = await send(api, sunita, hers, **{**BANK, "number": "123-456-789-012"})
    await post(api, f"/v1/partner-applications/{mine}/withdraw", priya)
    third = (await apply(api, ramesh, kind="packer")).json()["id"]
    after_withdrawal = await send(api, ramesh, third, **BANK)

    def check(answer: httpx2.Response) -> tuple[str, str]:
        assert answer.status_code == 201, answer.text
        return answer.json()["check"]["status"], answer.json()["check"]["code"]

    assert check(failed) == ("failed", "penny_drop_failed")
    assert check(first) == check(again_mine) == ("passed", "penny_drop_ok")
    assert check(same) == ("failed", "duplicate")
    # Priya withdrew: her number is on no open application or partner record any more, but
    # Sunita's open application still has it.
    assert check(after_withdrawal) == ("failed", "duplicate")


async def test_file_keys_must_be_the_callers_own_kyc_uploads(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = clients(storage=store_for(s3))
    no_storage = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    _, sunita_id = await as_user(api, google, SUNITA)
    application = (await apply(api, priya, kind="packer")).json()["id"]
    hers = await upload_key(db_session, s3, sunita_id)
    other_purpose = await upload_key(db_session, s3, priya_id, purpose="order_issue_photo")
    nothing_uploaded = await upload_key(db_session, s3, priya_id)
    del s3.objects[nothing_uploaded]
    mine = await upload_key(db_session, s3, priya_id)
    await db_session.commit()

    for key in (hers, other_purpose, nothing_uploaded, "kyc_document/x/y.jpg"):
        refused = await send(api, priya, application, **VOTER_ID, file_keys=[key])
        assert code(refused) == (422, "partners.file_key_invalid"), key
    unavailable = await send(no_storage, priya, application, **VOTER_ID, file_keys=[mine])
    three = await send(api, priya, application, **VOTER_ID, file_keys=[mine, hers, mine])

    assert code(unavailable) == (503, "platform.storage_unavailable")
    assert code(three) == (422, "partners.file_key_invalid")


async def test_a_replacement_supersedes_and_closing_sets_the_file_retention(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = clients(storage=store_for(s3))
    priya, priya_id = await as_user(api, google, PRIYA)
    application = (await apply(api, priya, kind="packer")).json()["id"]
    old_key = await upload_key(db_session, s3, priya_id)
    new_key = await upload_key(db_session, s3, priya_id)

    old = (await send(api, priya, application, **VOTER_ID, file_keys=[old_key])).json()
    new = (await send(api, priya, application, **VOTER_ID, file_keys=[new_key])).json()
    listed = await api.get(f"/v1/partner-applications/{application}", headers=priya)
    superseded = await db_session.get(KycDocument, uuid.UUID(old["id"]))
    kept = await db_session.get(Upload, new_key)
    released = await db_session.get(Upload, old_key)

    assert [d["id"] for d in listed.json()["documents"]] == [new["id"]]
    assert superseded is not None and str(superseded.superseded_by) == new["id"]
    assert kept is not None and kept.delete_after is None
    assert released is not None and released.delete_after is not None
    assert released.delete_after - datetime.now(UTC) > timedelta(days=29)

    await post(api, f"/v1/partner-applications/{application}/withdraw", priya)
    await db_session.refresh(kept)
    assert kept.delete_after is not None
    await db_session.commit()
    closed = await send(api, priya, application, **VOTER_ID)
    assert code(closed) == (409, "partners.application_closed")


async def test_file_links_are_for_that_hubs_staff_only_and_every_call_is_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = clients(storage=store_for(s3))
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)
    stranger = await other_hub_manager(api, google, db_session)
    application = (await apply(api, priya, kind="packer")).json()["id"]
    keys = [await upload_key(db_session, s3, priya_id) for _ in range(2)]
    document = (await send(api, priya, application, **VOTER_ID, file_keys=keys)).json()
    url = f"/v1/kyc-documents/{document['id']}/file-links"
    await db_session.commit()

    by_manager = await api.post(url, headers=vikram)
    by_admin = await api.post(url, headers=neha)
    by_stranger = await api.post(url, headers=stranger)
    by_applicant = await api.post(url, headers=priya)
    unknown = await api.post(f"/v1/kyc-documents/{uuid.uuid4()}/file-links", headers=vikram)

    assert by_manager.status_code == 200, by_manager.text
    links = by_manager.json()["files"]
    assert [link["content_type"] for link in links] == ["image/jpeg", "image/jpeg"]
    assert all(key in link["url"] for key, link in zip(keys, links, strict=True))
    expires = datetime.fromisoformat(links[0]["expires_at"])
    assert timedelta(minutes=4) < expires - datetime.now(UTC) <= timedelta(minutes=5)
    assert by_admin.status_code == 200
    assert code(by_stranger) == (404, "partners.document_not_found")
    assert code(by_applicant) == (403, "auth.forbidden")
    assert code(unknown) == (404, "partners.document_not_found")
    reads = (
        (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "kyc.file_read")))
        .scalars()
        .all()
    )
    assert len(reads) == 2
    assert (reads[0].actor_user_id, reads[0].entity_id) == (vikram_id, document["id"])
    assert reads[0].after == {"application_id": application, "files": 2}


async def test_the_data_export_has_masked_references_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    application = (await apply(api, priya, kind="packer")).json()["id"]
    await send(api, priya, application, **VOTER_ID)
    section = await partners_api.export_for_user(db_session, priya_id)

    (document,) = section["kyc_documents"]
    assert document["number_masked"] == "XXXXXX4567"
    assert not {"number_enc", "number_hash", "file_keys", "key_version"} & set(document)
    assert "ABC1234567" not in json.dumps(section, default=str)
