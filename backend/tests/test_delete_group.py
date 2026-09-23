import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from backend.app.routers.targets import delete_group


def test_delete_group_with_targets():
    mock_db = AsyncMock()

    # Step 1: select(Target.id).where(Target.group == group_name)
    # returns target_ids: [10, 11, 12]
    mock_id_rows = MagicMock()
    mock_id_rows.all.return_value = [(10,), (11,), (12,)]

    # Step 2: select(Screenshot.file_path, Screenshot.thumb_path)
    mock_shot_rows = MagicMock()
    mock_shot_rows.all.return_value = [("fake_shot_1.png", "fake_thumb_1.webp")]

    # Step 3: select(Snapshot.file_path)
    mock_snap_rows = MagicMock()
    mock_snap_rows.all.return_value = [("fake_snap_1.html.gz",)]

    # execute returns mock_id_rows, mock_shot_rows, mock_snap_rows, delete CheckResult, delete Target, delete GroupSetting
    mock_db.execute.side_effect = [
        mock_id_rows,
        mock_shot_rows,
        mock_snap_rows,
        MagicMock(),  # delete CheckResult
        MagicMock(),  # delete Target
        MagicMock(),  # delete GroupSetting
    ]

    with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.unlink") as mock_unlink:
        result = asyncio.run(delete_group("test_group", db=mock_db))

        assert result["ok"] is True
        assert result["group"] == "test_group"
        assert result["deleted_targets"] == 3
        assert mock_db.commit.called
        assert mock_unlink.call_count == 3  # 2 screenshot files + 1 snapshot file


def test_delete_group_empty():
    mock_db = AsyncMock()

    # No targets in group
    mock_id_rows = MagicMock()
    mock_id_rows.all.return_value = []

    mock_db.execute.side_effect = [
        mock_id_rows,
        MagicMock(),  # delete GroupSetting
    ]

    result = asyncio.run(delete_group("empty_group", db=mock_db))

    assert result["ok"] is True
    assert result["group"] == "empty_group"
    assert result["deleted_targets"] == 0
    assert mock_db.commit.called
