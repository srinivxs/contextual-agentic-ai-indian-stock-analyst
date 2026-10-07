"""The S3 blob store: where fetched PDFs live in AWS (go-live), a private bucket, not a folder.

The rest of the code sees only ``BlobStore``, so nothing else changes. These tests use a stand-in
for boto3's S3 client that records the calls; no request leaves the machine.
"""

import io
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.blobs import (
    BlobMissing,
    FilesystemBlobStore,
    InvalidBlobKey,
    S3BlobStore,
    blob_key_for,
    make_blob_store,
    s3_client,
)
from tests.helpers import build_settings

SHA = "cd" * 32
PDF = b"%PDF-1.7\n" + b"y" * 500


class FakeS3:
    """Just enough of boto3's S3 client: put_object and get_object, kept in a dict."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs["Body"]
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        if self.refuse is not None:
            raise ClientError({"Error": {"Code": self.refuse, "Message": "no"}}, "GetObject")
        found = self.objects.get((kwargs["Bucket"], kwargs["Key"]))
        if found is None:  # what S3 answers for a key it does not have
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")
        return {"Body": io.BytesIO(found)}

    refuse: str | None = None


async def test_a_file_that_is_not_in_the_bucket_is_said_to_be_missing() -> None:
    # The bucket is destroyed with the stack while the database is kept (the owner, 2026-10-08).
    with pytest.raises(BlobMissing):
        await S3BlobStore(bucket="demo-documents", client=FakeS3()).get(blob_key_for(SHA))


async def test_any_other_s3_error_is_not_mistaken_for_a_missing_file() -> None:
    s3 = FakeS3()
    s3.refuse = "AccessDenied"
    with pytest.raises(ClientError):
        await S3BlobStore(bucket="demo-documents", client=s3).get(blob_key_for(SHA))


async def test_what_is_put_can_be_read_back() -> None:
    s3 = FakeS3()
    store = S3BlobStore(bucket="demo-documents", client=s3)
    key = blob_key_for(SHA)

    await store.put(key, PDF)

    assert await store.get(key) == PDF
    assert s3.objects == {("demo-documents", f"documents/{SHA}.pdf"): PDF}


async def test_an_upload_is_marked_as_a_pdf() -> None:
    s3 = FakeS3()
    await S3BlobStore(bucket="demo-documents", client=s3).put(blob_key_for(SHA), PDF)
    assert s3.calls[0][1]["ContentType"] == "application/pdf"


async def test_putting_the_same_key_twice_is_harmless() -> None:
    """Same key, same bytes: a second or concurrent fetch writes identical content to one place."""
    s3 = FakeS3()
    store = S3BlobStore(bucket="demo-documents", client=s3)
    key = blob_key_for(SHA)

    await store.put(key, PDF)
    await store.put(key, PDF)

    assert len(s3.objects) == 1
    assert await store.get(key) == PDF


@pytest.mark.parametrize("key", ["../etc/passwd", "documents/x.pdf", f"other/{SHA}.pdf"])
async def test_a_key_that_is_not_ours_is_refused_before_any_request(key: str) -> None:
    s3 = FakeS3()
    store = S3BlobStore(bucket="demo-documents", client=s3)

    with pytest.raises(InvalidBlobKey):
        await store.put(key, PDF)
    with pytest.raises(InvalidBlobKey):
        await store.get(key)
    assert s3.calls == []


def test_without_a_bucket_filings_are_kept_on_disk(tmp_path: Path) -> None:
    store = make_blob_store(build_settings(blob_root=tmp_path))
    assert isinstance(store, FilesystemBlobStore)
    assert store.root == tmp_path


def test_with_a_bucket_filings_go_to_s3() -> None:
    store = make_blob_store(build_settings(blob_bucket="stock-analyst-demo-documents-123456789012"))
    assert isinstance(store, S3BlobStore)
    assert store.bucket == "stock-analyst-demo-documents-123456789012"


def test_no_bucket_is_set_by_default() -> None:
    assert build_settings().blob_bucket is None


@pytest.mark.parametrize("name", ["", "UPPER", "a", "has_underscore", "x" * 64, "-leading"])
def test_a_bucket_name_s3_would_refuse_is_refused_at_startup(name: str) -> None:
    with pytest.raises(ValidationError):
        build_settings(blob_bucket=name)


def test_the_s3_client_is_made_for_the_region_without_a_request() -> None:
    client = s3_client("ap-south-1")
    assert client.meta.region_name == "ap-south-1"
    assert client.meta.service_model.service_name == "s3"
