"""
Unit tests for backup functionality.

Run with: pytest tests/test_backup.py -v

Note: These tests mock Google Drive API calls to avoid requiring credentials.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set environment variables before importing
os.environ["GOOGLE_DRIVE_FOLDER_ID"] = "test-folder-id"
os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"] = "test-service-account.json"
os.environ["MEMORY_FILE_PATH"] = tempfile.mktemp(suffix=".md")
os.environ["BACKUP_RETENTION_DAYS"] = "30"


class TestBackupConfiguration:
    """Tests for backup configuration."""

    def test_retention_days_from_env(self):
        """Test that retention days are read from environment."""
        from backup import BACKUP_RETENTION_DAYS
        assert BACKUP_RETENTION_DAYS == 30

    def test_folder_id_from_env(self):
        """Test that folder ID is read from environment."""
        from backup import PARENT_FOLDER_ID
        assert PARENT_FOLDER_ID == "test-folder-id"


class TestBackupFunctions:
    """Tests for backup functions with mocked Google API."""

    @pytest.fixture
    def mock_drive_service(self):
        """Create a mock Google Drive service."""
        mock_service = MagicMock()

        # Mock files().create()
        mock_create = MagicMock()
        mock_create.execute.return_value = {
            "id": "test-file-id",
            "name": "AGENTS_backup_2024-01-01_12-00-00.md",
            "size": "1024",
            "createdTime": "2024-01-01T12:00:00Z"
        }
        mock_service.files.return_value.create.return_value = mock_create

        # Mock files().list()
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "files": [
                {
                    "id": "file-1",
                    "name": "AGENTS_backup_2024-01-01_12-00-00.md",
                    "size": "1024",
                    "createdTime": "2024-01-01T12:00:00Z"
                },
                {
                    "id": "file-2",
                    "name": "AGENTS_backup_2024-01-02_12-00-00.md",
                    "size": "2048",
                    "createdTime": "2024-01-02T12:00:00Z"
                }
            ]
        }
        mock_service.files.return_value.list.return_value = mock_list

        # Mock files().delete()
        mock_delete = MagicMock()
        mock_delete.execute.return_value = None
        mock_service.files.return_value.delete.return_value = mock_delete

        return mock_service

    @pytest.fixture
    def temp_memory_file(self):
        """Create a temporary memory file for testing."""
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write("# Test Memory\n\n- Test entry\n")
        yield path
        if os.path.exists(path):
            os.remove(path)

    def test_backup_to_gdrive_success(self, mock_drive_service, temp_memory_file):
        """Test successful backup to Google Drive."""
        import backup
        backup.FILE_TO_BACKUP = temp_memory_file

        with patch.object(backup, "get_drive_service", return_value=mock_drive_service):
            result = backup.backup_to_gdrive()

        assert result == "test-file-id"
        mock_drive_service.files.return_value.create.assert_called_once()

    def test_backup_missing_folder_id(self, temp_memory_file):
        """Test backup fails gracefully when folder ID is missing."""
        import backup
        original_folder_id = backup.PARENT_FOLDER_ID
        backup.PARENT_FOLDER_ID = None
        backup.FILE_TO_BACKUP = temp_memory_file

        try:
            result = backup.backup_to_gdrive()
            assert result is None
        finally:
            backup.PARENT_FOLDER_ID = original_folder_id

    def test_backup_missing_file(self, mock_drive_service):
        """Test backup fails gracefully when source file is missing."""
        import backup
        backup.FILE_TO_BACKUP = "/nonexistent/path/to/file.md"

        result = backup.backup_to_gdrive()
        assert result is None

    def test_list_backups(self, mock_drive_service):
        """Test listing backups from Google Drive."""
        import backup

        with patch.object(backup, "get_drive_service", return_value=mock_drive_service):
            backups = backup.list_backups()

        assert len(backups) == 2
        assert backups[0]["id"] == "file-1"

    def test_get_backup_stats(self, mock_drive_service):
        """Test getting backup statistics."""
        import backup

        with patch.object(backup, "get_drive_service", return_value=mock_drive_service):
            stats = backup.get_backup_stats()

        assert stats["count"] == 2
        assert stats["total_size_bytes"] == 3072  # 1024 + 2048
        assert "oldest" in stats
        assert "newest" in stats

    def test_cleanup_old_backups(self, mock_drive_service):
        """Test cleaning up old backups."""
        import backup

        # Mock list to return old files
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "files": [
                {
                    "id": "old-file-1",
                    "name": "AGENTS_backup_old.md",
                    "createdTime": old_date
                }
            ]
        }
        mock_drive_service.files.return_value.list.return_value = mock_list

        with patch.object(backup, "get_drive_service", return_value=mock_drive_service):
            deleted = backup.cleanup_old_backups(days_to_keep=30)

        assert deleted == 1
        mock_drive_service.files.return_value.delete.assert_called_once()


class TestBackupCLI:
    """Tests for backup CLI commands."""

    def test_backup_module_runnable(self):
        """Test that backup module can be imported."""
        import backup
        assert hasattr(backup, "run_backup_job")
        assert hasattr(backup, "backup_to_gdrive")
        assert hasattr(backup, "cleanup_old_backups")
        assert hasattr(backup, "list_backups")
        assert hasattr(backup, "get_backup_stats")
