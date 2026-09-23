import asyncio
import gzip
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from backend.app.routers.snapshots import _read_snapshot_html, get_snapshot_diff
from backend.app.models import Snapshot, Target


def test_read_snapshot_html_gz(tmp_path):
    # Test reading gzipped snapshot file
    test_content = "<html><body><h1>Hello Wayback</h1></body></html>"
    gz_file = tmp_path / "test.html.gz"
    with gzip.open(gz_file, "wt", encoding="utf-8") as f:
        f.write(test_content)

    with patch("backend.app.routers.snapshots.settings") as mock_settings:
        mock_settings.snapshots_dir = str(tmp_path)
        read_back = _read_snapshot_html("test.html.gz")
        assert read_back == test_content


def test_read_snapshot_html_plain(tmp_path):
    test_content = "<html><body><h1>Plain Snapshot</h1></body></html>"
    plain_file = tmp_path / "test.html"
    plain_file.write_text(test_content, encoding="utf-8")

    with patch("backend.app.routers.snapshots.settings") as mock_settings:
        mock_settings.snapshots_dir = str(tmp_path)
        read_back = _read_snapshot_html("test.html")
        assert read_back == test_content


def test_snapshot_has_changed_logic():
    # Test hash detection
    content1 = "<html><body>Version 1</body></html>"
    content2 = "<html><body>Version 1</body></html>"
    content3 = "<html><body>Version 2</body></html>"

    h1 = hashlib.sha256(content1.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(content2.encode("utf-8")).hexdigest()
    h3 = hashlib.sha256(content3.encode("utf-8")).hexdigest()

    assert h1 == h2
    assert h1 != h3


def test_snapshot_view_banner_injection():
    # Test that base tag and wayback bar are properly injected
    from backend.app.routers.snapshots import view_snapshot

    mock_db = AsyncMock()
    mock_target = MagicMock(id=1, url="https://example.com/test", name="Example Site")
    mock_snapshot = MagicMock(
        id=10,
        target_id=1,
        taken_at=MagicMock(strftime=lambda fmt: "2026-09-23 11:30:00"),
        file_path="dummy.html.gz",
        file_size=1024,
        has_changed=True,
        http_status=200,
    )

    mock_row = MagicMock()
    mock_row.first.return_value = (mock_snapshot, mock_target)

    # Database returns mock_row, prev_id (None), next_id (None)
    mock_db.execute.side_effect = [
        mock_row,
        MagicMock(scalar_one_or_none=lambda: None),
        MagicMock(scalar_one_or_none=lambda: None),
    ]

    sample_html = "<html><head><title>Test Page</title></head><body><h1>Content</h1></body></html>"

    with patch("backend.app.routers.snapshots._read_snapshot_html", return_value=sample_html):
        resp = asyncio.run(view_snapshot(10, db=mock_db))
        body = resp.body.decode("utf-8")

        # Must have <base href="...">
        assert '<base href="https://example.com/test"' in body
        # Must have Wayback Machine banner
        assert '__sm_wayback_bar__' in body
        assert '网页时光机' in body
        assert '2026-09-23 11:30:00' in body
