"""The filesystem blob store: where fetched PDFs live on a laptop (S3 takes its place in AWS, P9c).

A key is derived from the file's SHA-256, so the same bytes always land at the same key. That is
what makes a second or concurrent fetch of one file harmless: every writer writes identical bytes
to one place.
"""

import threading
from pathlib import Path

import pytest

from app.blobs import FilesystemBlobStore, InvalidBlobKey, blob_key_for

SHA = "ab" * 32
PDF = b"%PDF-1.7\n" + b"x" * 1000


def test_the_key_is_derived_from_the_content_hash() -> None:
    assert blob_key_for(SHA) == f"documents/{SHA}.pdf"


@pytest.mark.parametrize("digest", ["", "AB" * 32, "ab" * 31, "zz" * 32, "../" + "a" * 61])
def test_a_key_is_only_made_from_a_real_sha256(digest: str) -> None:
    with pytest.raises(InvalidBlobKey):
        blob_key_for(digest)


async def test_what_is_put_can_be_read_back(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path)
    key = blob_key_for(SHA)

    await store.put(key, PDF)

    assert await store.get(key) == PDF
    assert (tmp_path / "documents" / f"{SHA}.pdf").read_bytes() == PDF


async def test_putting_the_same_key_twice_is_harmless(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path)
    key = blob_key_for(SHA)

    await store.put(key, PDF)
    await store.put(key, PDF)

    assert await store.get(key) == PDF


async def test_no_temporary_file_is_left_behind(tmp_path: Path) -> None:
    store = FilesystemBlobStore(tmp_path)
    await store.put(blob_key_for(SHA), PDF)

    assert sorted(p.name for p in (tmp_path / "documents").iterdir()) == [f"{SHA}.pdf"]


def test_concurrent_writers_of_one_key_all_succeed_and_leave_one_whole_file(
    tmp_path: Path,
) -> None:
    """Eight writes of one file at once: nobody fails, and nobody reads half a file."""
    store = FilesystemBlobStore(tmp_path)
    key = blob_key_for(SHA)
    errors: list[BaseException] = []

    def write() -> None:
        try:
            store.put_sync(key, PDF)
        except BaseException as error:  # collected and asserted below
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert (tmp_path / "documents" / f"{SHA}.pdf").read_bytes() == PDF
    assert len(list((tmp_path / "documents").iterdir())) == 1


@pytest.mark.parametrize(
    "key",
    ["../outside.pdf", "/etc/passwd", "documents/../../x.pdf", "documents/x.pdf", "C:/x.pdf", ""],
)
async def test_a_key_that_is_not_ours_is_refused_before_touching_the_disk(
    tmp_path: Path, key: str
) -> None:
    store = FilesystemBlobStore(tmp_path / "root")

    with pytest.raises(InvalidBlobKey):
        await store.put(key, PDF)
    with pytest.raises(InvalidBlobKey):
        await store.get(key)
    assert not (tmp_path / "root").exists() or not any((tmp_path / "root").rglob("*"))


def test_a_real_write_failure_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PermissionError is forgiven only when another writer has already put the file in place.

    If the rename fails and there is still no file, the write must fail loudly, and the temporary
    file must not be left behind."""
    store = FilesystemBlobStore(tmp_path)

    def refuse(source: str, target: str) -> None:
        raise PermissionError("disk says no")

    monkeypatch.setattr("app.blobs.os.replace", refuse)

    with pytest.raises(PermissionError):
        store.put_sync(blob_key_for(SHA), PDF)
    assert list((tmp_path / "documents").iterdir()) == []
