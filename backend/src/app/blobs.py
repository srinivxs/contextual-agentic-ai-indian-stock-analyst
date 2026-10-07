"""Where fetched filings are kept: a folder on a laptop, a private S3 bucket in AWS (go-live).

The rest of the code sees only ``BlobStore``: put bytes under a key, get them back. Tests use a
temporary folder or a stand-in S3 client. ``make_blob_store`` picks one from the settings: with
``BLOB_BUCKET`` set, the bucket; without it, the folder.

A key is derived from the file's SHA-256 (``documents/<sha256>.pdf``). The same bytes always land
at the same key, which is what makes duplicate and concurrent fetches harmless: every writer writes
identical bytes to one place. And because we build every key ourselves, nothing the client sends
(a filename, say) ever becomes part of a path.
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import CommonSettings

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^documents/[0-9a-f]{64}\.pdf$")


class InvalidBlobKey(ValueError):
    """A key we did not build. Refused before any path is formed from it."""


class BlobMissing(LookupError):
    """No file under this key. In AWS the documents bucket is destroyed with the stack while the
    database is kept as a snapshot (the owner, 2026-10-08), so a filing fetched in an earlier
    session but never read has lost its file. The caller decides what that means."""


def blob_key_for(sha256: str) -> str:
    if not _SHA256.match(sha256):
        raise InvalidBlobKey("a blob key is built from a lower-case hex SHA-256")
    return f"documents/{sha256}.pdf"


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...


class FilesystemBlobStore:
    """Files under one root folder. The folder is created on the first write, not at startup."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        _check(key)
        return self.root / key

    async def put(self, key: str, data: bytes) -> None:
        self._path(key)  # validate before handing off to a thread
        await asyncio.to_thread(self.put_sync, key, data)

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._path(key).read_bytes)
        except FileNotFoundError as error:
            raise BlobMissing(key) from error

    def put_sync(self, key: str, data: bytes) -> None:
        """Write atomically: a temporary file in the same folder, then one rename.

        A reader therefore sees either no file or the whole file, never half of one. Concurrent
        writers of one key write identical bytes, so whichever rename lands last changes nothing.
        """
        target = self._path(key)
        if target.exists():
            return  # same key, same bytes: already there
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".upload-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
            try:
                os.replace(temporary, target)
            except PermissionError:
                # Windows refuses to replace a file another writer has just renamed into place.
                # That writer wrote the same bytes, so the job is done.
                if not target.exists():
                    raise
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


class S3BlobStore:
    """Objects in one private bucket (go-live). boto3 is synchronous, so each call runs in a thread.

    No "is it there already?" check: the same key always holds the same bytes, so writing it again
    changes nothing, and one request is cheaper than two. The bucket encrypts every object itself.
    """

    def __init__(self, *, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self._client = client

    async def put(self, key: str, data: bytes) -> None:
        _check(key)
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/pdf",
        )

    async def get(self, key: str) -> bytes:
        _check(key)
        try:
            response = await asyncio.to_thread(self._client.get_object, Bucket=self.bucket, Key=key)
        except ClientError as error:
            # Only "no such key" is a missing file; anything else (access denied, a throttle)
            # is a real failure and is raised as it is, so the job is retried.
            if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise BlobMissing(key) from error
            raise
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body


def _check(key: str) -> None:
    if not _KEY.match(key):
        raise InvalidBlobKey("not a key this application issued")


def s3_client(region: str) -> Any:
    """An S3 client for the task's own role. Creating it needs no credentials or request."""
    config = Config(connect_timeout=5, read_timeout=60, retries={"mode": "standard"})
    return boto3.client("s3", region_name=region, config=config)


def make_blob_store(settings: CommonSettings) -> BlobStore:
    """The bucket when BLOB_BUCKET is set (AWS), otherwise the folder (a laptop, the tests)."""
    if settings.blob_bucket:
        return S3BlobStore(bucket=settings.blob_bucket, client=s3_client(settings.aws_region))
    return FilesystemBlobStore(settings.blob_root)
