"""The two settings P9 adds: where uploaded files are kept, and how large one may be."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.helpers import build_settings

REPO = Path(__file__).resolve().parents[3]


def test_uploads_are_kept_in_the_git_ignored_data_folder_by_default() -> None:
    settings = build_settings()
    assert settings.blob_root == REPO / "data" / "local" / "blobs"
    assert "data/local/" in (REPO / ".gitignore").read_text(encoding="utf-8")


def test_the_default_upload_limit_is_twenty_megabytes() -> None:
    assert build_settings().upload_max_bytes == 20 * 1024 * 1024


@pytest.mark.parametrize("value", [0, 1023, 100 * 1024 * 1024 + 1])
def test_an_unreasonable_upload_limit_is_refused_at_startup(value: int) -> None:
    with pytest.raises(ValidationError):
        build_settings(upload_max_bytes=value)


def test_building_the_app_creates_no_folder(tmp_path: Path) -> None:
    """Constructing the app has no side effects; the folder appears on the first upload."""
    from app.main import create_app

    create_app(build_settings(blob_root=tmp_path / "blobs"))
    assert not (tmp_path / "blobs").exists()
