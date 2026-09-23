"""Where fetched filings are kept (P9), and that the api no longer has anywhere to store one."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.helpers import build_settings

REPO = Path(__file__).resolve().parents[3]


def test_filings_are_kept_in_the_git_ignored_data_folder_by_default() -> None:
    settings = build_settings()
    assert settings.blob_root == REPO / "data" / "local" / "blobs"
    assert "data/local/" in (REPO / ".gitignore").read_text(encoding="utf-8")


def test_the_upload_limit_is_gone() -> None:
    """Uploads were removed in P9d; a stale UPLOAD_MAX_BYTES is simply ignored, not an error."""
    assert not hasattr(build_settings(), "upload_max_bytes")


def test_the_api_has_no_blob_store(tmp_path: Path) -> None:
    """Only the worker writes files. The api reads metadata from the database and nothing else."""
    from app.main import create_app

    app = create_app(build_settings(blob_root=tmp_path / "blobs"))
    assert not hasattr(app.state, "blob_store")
    assert not (tmp_path / "blobs").exists()


def test_an_unreasonable_filing_limit_is_refused_at_startup() -> None:
    with pytest.raises(ValidationError):
        build_settings(filings_max_bytes=0)
