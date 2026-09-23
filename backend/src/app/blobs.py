"""Where uploaded files are kept: a folder on a laptop, a private S3 bucket in AWS (P9c).

The rest of the code sees only ``BlobStore``: put bytes under a key, get them back. Tests use a
temporary folder, and the S3 version arrives without anything else changing.

A key is derived from the file's SHA-256 (``documents/<sha256>.pdf``). The same bytes always land
at the same key, which is what makes duplicate and concurrent uploads harmless: every writer writes
identical bytes to one place. And because we build every key ourselves, nothing the client sends
(a filename, say) ever becomes part of a path.
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY = re.compile(r"^documents/[0-9a-f]{64}\.pdf$")


class InvalidBlobKey(ValueError):
    """A key we did not build. Refused before any path is formed from it."""


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
        if not _KEY.match(key):
            raise InvalidBlobKey("not a key this application issued")
        return self.root / key

    async def put(self, key: str, data: bytes) -> None:
        self._path(key)  # validate before handing off to a thread
        await asyncio.to_thread(self.put_sync, key, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

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
