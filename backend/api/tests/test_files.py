"""Private uploads (M0 §3.1): presign, the claim facade, signed reads and the sweep job."""

import re
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from botocore.exceptions import ClientError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform import files
from gharbasket.platform.db import get_session
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.models import Upload
from gharbasket.platform.tokens import issue_access_token
from gharbasket.platform.valkey import create_valkey

from .commit_guard import CommitGuard
from .conftest import make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]

ME = uuid.UUID("0199a1b2-0000-7000-8000-00000000f001")
OTHER = uuid.UUID("0199a1b2-0000-7000-8000-00000000f002")
SETTINGS = make_settings(
    s3_endpoint_url="http://seaweedfs:8333",
    s3_public_endpoint_url="http://10.0.2.2:8333",
    s3_access_key="test-access",
    s3_secret_key="test-storage-secret",
)
MiB = 1024 * 1024


class FakeS3:
    """Just enough of a boto3 client for head and delete: objects in a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, int]] = {}
        self.deletes: list[list[str]] = []
        self.puts: list[dict[str, str]] = []
        self.fail_head = False

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if self.fail_head:
            raise ClientError({"Error": {"Code": "500"}}, "HeadObject")
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        content_type, size = self.objects[Key]
        return {"ContentType": content_type, "ContentLength": size}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **extra: str) -> dict[str, Any]:
        self.objects[Key] = (extra["ContentType"], len(Body))
        self.puts.append({"Key": Key, **extra})
        return {}

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        keys = [item["Key"] for item in Delete["Objects"]]
        self.deletes.append(keys)
        for key in keys:
            self.objects.pop(key, None)  # S3: deleting a missing key is not an error
        return {}


@pytest.fixture
def s3() -> FakeS3:
    return FakeS3()


@pytest.fixture
def store(s3: FakeS3) -> ObjectStore:
    found = create_store(SETTINGS)
    assert found is not None
    found.client = s3  # real boto3 signs URLs (offline); the fake answers head and delete
    return found


@pytest.fixture
async def valkey(valkey_url: str) -> AsyncIterator[Redis]:
    client = create_valkey(valkey_url)
    await client.flushdb()
    yield client
    await client.aclose()


Factory = Callable[..., httpx2.AsyncClient]


@pytest.fixture
async def make_api(commit_guard: CommitGuard, store: ObjectStore) -> AsyncIterator[Factory]:
    opened: list[httpx2.AsyncClient] = []
    session_override = commit_guard.override()

    def make(
        *, storage: ObjectStore | None = store, valkey: Redis | None = None, user=ME
    ) -> httpx2.AsyncClient:
        app = create_app(SETTINGS)
        app.dependency_overrides[get_session] = session_override
        app.state.storage, app.state.valkey = storage, valkey
        token, _ = issue_access_token(
            SETTINGS, user_id=user, session_id=uuid.uuid4(), roles=("customer",)
        )
        client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://t",
            headers={"Authorization": f"Bearer {token}"},
        )
        opened.append(client)
        return client

    yield make
    for client in opened:
        await client.aclose()


def _presign(purpose: str, content_type: str, size_bytes: int) -> dict[str, Any]:
    return {"purpose": purpose, "content_type": content_type, "size_bytes": size_bytes}


@pytest.mark.parametrize(
    ("purpose", "content_type", "size", "ext"),
    [
        ("order_issue_photo", "image/jpeg", 834221, "jpg"),
        ("donation_photo", "image/webp", 5 * MiB, "webp"),
        ("kyc_document", "application/pdf", 10 * MiB, "pdf"),
        ("delivery_photo", "image/webp", 5 * MiB, "webp"),
        ("handoff_photo", "image/png", 5 * MiB, "png"),
    ],
)
async def test_presign_signs_a_put_for_each_purpose(
    make_api: Factory,
    db_session: AsyncSession,
    purpose: str,
    content_type: str,
    size: int,
    ext: str,
) -> None:
    before = datetime.now(UTC).replace(microsecond=0)
    answer = await make_api().post("/v1/files/presign", json=_presign(purpose, content_type, size))
    after = datetime.now(UTC)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert re.fullmatch(rf"{purpose}/{ME}/[0-9a-f-]{{36}}\.{ext}", body["key"])
    assert uuid.UUID(body["key"].split("/")[2].removesuffix(f".{ext}")).version == 7
    assert body["method"] == "PUT"
    assert body["headers"] == {"Content-Type": content_type, "Content-Length": str(size)}
    expires_at = datetime.fromisoformat(body["expires_at"])
    # Bounded by the request itself, not a fixed allowance: a loaded machine can take seconds.
    assert before + timedelta(minutes=10) <= expires_at <= after + timedelta(minutes=10, seconds=1)
    url = urlsplit(body["url"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}" == "http://10.0.2.2:8333"  # S3_PUBLIC_ENDPOINT_URL
    assert url.path == f"/gb-private/{body['key']}"
    assert query["X-Amz-Expires"] == ["600"]
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]
    row = await db_session.get(Upload, body["key"])
    assert row is not None
    assert (row.user_id, row.purpose, row.content_type, row.size_bytes) == (
        ME,
        purpose,
        content_type,
        size,
    )
    assert row.claimed_by is None and row.delete_after is None


@pytest.mark.parametrize(
    ("body", "code", "extra"),
    [
        (_presign("selfie", "image/jpeg", 10), "platform.upload_purpose_invalid", {}),
        (_presign("data_export", "application/json", 10), "platform.upload_purpose_invalid", {}),
        (
            _presign("income_certificate", "application/pdf", 10),
            "platform.upload_purpose_invalid",
            {},
        ),
        (
            _presign("kyc_document", "image/webp", 10),
            "platform.upload_type_not_allowed",
            {"allowed": ["image/jpeg", "image/png", "application/pdf"]},
        ),
        (
            _presign("order_issue_photo", "image/jpeg", 5 * MiB + 1),
            "platform.upload_too_large",
            {"max_bytes": 5 * MiB},
        ),
        (
            _presign("kyc_document", "application/pdf", 10 * MiB + 1),
            "platform.upload_too_large",
            {"max_bytes": 10 * MiB},
        ),
        (_presign("donation_photo", "image/png", 0), "platform.upload_too_large", {}),
        (
            _presign("delivery_photo", "image/png", 10),
            "platform.upload_type_not_allowed",
            {"allowed": ["image/jpeg", "image/webp"]},
        ),
        (
            _presign("delivery_photo", "image/jpeg", 5 * MiB + 1),
            "platform.upload_too_large",
            {"max_bytes": 5 * MiB},
        ),
        (
            _presign("handoff_photo", "application/pdf", 10),
            "platform.upload_type_not_allowed",
            {"allowed": ["image/jpeg", "image/png", "image/webp"]},
        ),
        (
            _presign("handoff_photo", "image/webp", 5 * MiB + 1),
            "platform.upload_too_large",
            {"max_bytes": 5 * MiB},
        ),
    ],
)
async def test_wrong_purpose_type_or_size_is_422_and_writes_nothing(
    make_api: Factory, db_session: AsyncSession, body: dict[str, Any], code: str, extra: dict
) -> None:
    answer = await make_api().post("/v1/files/presign", json=body)

    assert answer.status_code == 422
    assert answer.headers["content-type"].startswith("application/problem+json")
    assert answer.json()["code"] == code
    assert extra.items() <= answer.json().items()
    assert (await db_session.execute(select(Upload))).first() is None


async def test_presign_needs_sign_in_and_configured_storage(make_api: Factory) -> None:
    unsigned = await make_api().post(
        "/v1/files/presign",
        json=_presign("donation_photo", "image/jpeg", 10),
        headers={"Authorization": ""},
    )
    no_storage = await make_api(storage=None).post(
        "/v1/files/presign", json=_presign("donation_photo", "image/jpeg", 10)
    )

    assert unsigned.status_code == 401
    assert no_storage.status_code == 503
    assert no_storage.json()["code"] == "platform.storage_unavailable"
    assert create_store(make_settings()) is None


async def test_presign_is_limited_to_30_per_10_minutes_per_user(
    make_api: Factory, valkey: Redis
) -> None:
    mine, theirs = make_api(valkey=valkey), make_api(valkey=valkey, user=OTHER)
    body = _presign("donation_photo", "image/jpeg", 1000)
    statuses = [(await mine.post("/v1/files/presign", json=body)).status_code for _ in range(30)]

    limited = await mine.post("/v1/files/presign", json=body)
    other_user = await theirs.post("/v1/files/presign", json=body)

    assert statuses == [200] * 30
    assert limited.status_code == 429
    assert limited.json()["code"] == "platform.upload_rate_limited"
    assert 0 < int(limited.headers["Retry-After"]) <= 600
    assert other_user.status_code == 200


async def test_owning_modules_verify_and_claim_only_the_uploaders_own_key(
    make_api: Factory, db_session: AsyncSession
) -> None:
    key = (
        await make_api().post(
            "/v1/files/presign", json=_presign("order_issue_photo", "image/png", 2048)
        )
    ).json()["key"]
    photo = "order_issue_photo"

    assert await files.verify(db_session, key, user_id=ME, purpose=photo) is not None
    assert await files.verify(db_session, key, user_id=OTHER, purpose=photo) is None
    assert await files.verify(db_session, key, user_id=ME, purpose="kyc_document") is None
    assert await files.verify(db_session, "nope", user_id=ME, purpose=photo) is None
    assert await files.claim(db_session, key, user_id=OTHER, purpose=photo, by="orders") is None
    assert await files.set_delete_after(db_session, key, datetime.now(UTC)) is False  # unclaimed

    claimed = await files.claim(db_session, key, user_id=ME, purpose=photo, by="orders")
    again = await files.claim(db_session, key, user_id=ME, purpose=photo, by="orders")
    elsewhere = await files.claim(db_session, key, user_id=ME, purpose=photo, by="community")
    keep_until = datetime(2027, 1, 1, tzinfo=UTC)
    assert await files.set_delete_after(db_session, key, keep_until) is True

    assert claimed is not None and claimed.claimed_by == "orders" and claimed.claimed_at
    assert again is claimed
    assert elsewhere is None
    assert claimed.delete_after == keep_until


async def test_signed_get_links_last_5_minutes_and_head_reads_the_object(
    store: ObjectStore, s3: FakeS3
) -> None:
    key = f"kyc_document/{ME}/0199a1b2-0000-7000-8000-000000000abc.pdf"
    s3.objects[key] = ("application/pdf", 4096)

    url = urlsplit(store.signed_get(key))
    found = await store.head(key)
    missing = await store.head(key + "x")
    s3.fail_head = True
    with pytest.raises(ProblemError) as down:
        await store.head(key)

    assert f"{url.scheme}://{url.netloc}{url.path}" == f"http://10.0.2.2:8333/gb-private/{key}"
    assert parse_qs(url.query)["X-Amz-Expires"] == ["300"]
    assert parse_qs(url.query)["X-Amz-SignedHeaders"] == ["host"]
    assert found == files.ObjectInfo("application/pdf", 4096)
    assert missing is None
    assert down.value.status == 503 and down.value.code == "platform.storage_unavailable"


async def test_sweep_deletes_unclaimed_and_expired_uploads_once(
    make_api: Factory, db_session: AsyncSession, s3: FakeS3
) -> None:
    when = datetime(2026, 11, 2, 22, 43, tzinfo=UTC)
    rows = {
        "fresh_unclaimed": (when - timedelta(hours=23), None, None),
        "old_unclaimed": (when - timedelta(hours=25), None, None),
        "claimed_kept": (when - timedelta(days=60), "orders", None),
        "claimed_expired": (when - timedelta(days=100), "orders", when - timedelta(minutes=1)),
        "claimed_later": (when - timedelta(days=100), "orders", when + timedelta(days=1)),
    }
    for name, (created_at, claimed_by, delete_after) in rows.items():
        key = f"order_issue_photo/{ME}/{name}.jpg"
        s3.objects[key] = ("image/jpeg", 10)
        db_session.add(
            Upload(
                key=key,
                user_id=ME,
                purpose="order_issue_photo",
                content_type="image/jpeg",
                size_bytes=10,
                created_at=created_at,
                claimed_by=claimed_by,
                claimed_at=created_at if claimed_by else None,
                delete_after=delete_after,
            )
        )
    await db_session.flush()
    api = make_api()

    first = await api.post(
        "/v1/internal/jobs/sweep_uploads", json={"scheduled_for": when.isoformat()}
    )
    second = await api.post(
        "/v1/internal/jobs/sweep_uploads", json={"scheduled_for": when.isoformat()}
    )

    assert first.status_code == 200, first.text
    assert first.json()["summary"] == {"unclaimed": 1, "expired": 1}
    assert second.json()["summary"] == {"unclaimed": 0, "expired": 0}
    left = {k.split("/")[2].removesuffix(".jpg") for k in s3.objects}
    assert left == {"fresh_unclaimed", "claimed_kept", "claimed_later"}
    in_db = (await db_session.execute(select(Upload.key))).scalars().all()
    assert {k.split("/")[2].removesuffix(".jpg") for k in in_db} == left
    assert len(s3.deletes) == 1


async def test_a_server_written_object_keeps_its_download_name(
    store: ObjectStore, s3: FakeS3
) -> None:
    disposition = 'attachment; filename="gharbasket-income-certificate-2026-05-to-2026-10.pdf"'

    await store.put("income_certificate/u/1.pdf", b"%PDF", "application/pdf")
    await store.put(
        "income_certificate/u/2.pdf", b"%PDF", "application/pdf", content_disposition=disposition
    )

    assert s3.puts == [
        {"Key": "income_certificate/u/1.pdf", "ContentType": "application/pdf"},
        {
            "Key": "income_certificate/u/2.pdf",
            "ContentType": "application/pdf",
            "ContentDisposition": disposition,
        },
    ]


async def test_the_sweep_fails_visibly_without_storage(make_api: Factory) -> None:
    answer = await make_api(storage=None).post("/v1/internal/jobs/sweep_uploads", json={})

    assert answer.status_code == 500
    assert answer.json()["code"] == "platform.job_failed"
