import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    from b2sdk.v2 import B2Api, InMemoryAccountInfo
except ImportError:  # b2sdk is an optional dependency
    B2Api = None  # type: ignore
    InMemoryAccountInfo = None  # type: ignore

from . import config

logger = logging.getLogger(__name__)


class B2BackupManager:
    """Manages Backblaze B2 backups for database files."""

    def __init__(self):
        self.api: B2Api | None = None
        self.bucket = None
        self._authorized = False

    async def authorize(self) -> bool:
        """
        Authorize with B2 using application keys.

        Returns:
            True if authorization successful, False otherwise.
        """
        if self._authorized:
            return True

        if not config.is_backup_configured():
            logger.warning("B2 backup not configured - missing credentials")
            return False

        try:
            info = InMemoryAccountInfo()
            self.api = B2Api(info)
            await asyncio.to_thread(
                self.api.authorize_account, "production", config.B2_KEY_ID, config.B2_APP_KEY
            )
            self.bucket = await asyncio.to_thread(self.api.get_bucket_by_name, config.B2_BUCKET)
            self._authorized = True

            logger.info(f"✅ B2 authorized - connected to bucket: {config.B2_BUCKET}")
            return True

        except Exception as e:
            logger.error(f"❌ B2 authorization failed: {e}")
            return False

    def get_database_files(self) -> list[Path]:
        """Get all database files from the data directory."""
        data_dir = Path(config.DATA_DIR)

        if not data_dir.exists():
            logger.warning(f"Data directory not found: {data_dir}")
            return []

        # Find all .db files
        db_files = list(data_dir.glob("*.db"))

        # Also check for .db-journal files (WAL files)
        journal_files = list(data_dir.glob("*.db-journal"))
        wal_files = list(data_dir.glob("*.db-wal"))
        shm_files = list(data_dir.glob("*.db-shm"))

        all_files = db_files + journal_files + wal_files + shm_files

        logger.info(f"📁 Found {len(all_files)} database files to backup")
        return sorted(all_files)

    def calculate_file_hash(self, file_path: Path) -> str:
        """Calculate MD5 hash of a file for integrity checking."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    async def backup_file(self, file_path: Path, prefix: str = "") -> str | None:
        """
        Upload a single file to B2.

        Args:
            file_path: Path to the file to upload
            prefix: Optional prefix for the backup path

        Returns:
            File ID if successful, None otherwise.
        """
        if not self._authorized:
            logger.error("Cannot backup - not authorized with B2")
            return None

        try:
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            backup_key = f"{config.BACKUP_PREFIX}/{timestamp}/{file_path.name}"

            if prefix:
                backup_key = f"{prefix}/{backup_key}"

            # Hashing + the b2sdk upload are both blocking; off-load to a thread.
            file_hash = await asyncio.to_thread(self.calculate_file_hash, file_path)
            file_version = await asyncio.to_thread(
                self.bucket.upload_local_file,
                local_file=str(file_path),
                file_name=backup_key,
                file_infos={
                    "upload_time": datetime.now(UTC).isoformat(),
                    "original_path": str(file_path),
                    "file_hash": file_hash,
                },
            )

            logger.info(f"✅ Uploaded: {file_path.name} → {backup_key}")
            return file_version.id_

        except Exception as e:
            logger.error(f"❌ Failed to upload {file_path.name}: {e}")
            return None

    async def backup_all_databases(self, prefix: str = "") -> dict:
        """
        Backup all database files.

        Args:
            prefix: Optional prefix for backup path

        Returns:
            Dictionary with backup results.
        """
        if not await self.authorize():
            return {"success": False, "error": "B2 not authorized", "files": []}

        files = self.get_database_files()

        if not files:
            return {"success": False, "error": "No database files found", "files": []}

        uploaded = []
        failed = []

        for file_path in files:
            file_id = await self.backup_file(file_path, prefix)
            if file_id:
                uploaded.append({"name": file_path.name, "id": file_id})
            else:
                failed.append(file_path.name)

        return {
            "success": len(failed) == 0,
            "timestamp": datetime.now(UTC).isoformat(),
            "uploaded": uploaded,
            "failed": failed,
            "total": len(files),
        }

    def list_backups(self, max_files: int = 100) -> list[dict]:
        """
        List available backups in B2.

        Args:
            max_files: Maximum number of files to list.

        Returns:
            List of backup file information.
        """
        if not self._authorized and not self.api:
            logger.error("Cannot list backups - not authorized")
            return []

        try:
            backups = []
            for file_version, _folder in self.bucket.ls(
                folder_to_list=f"{config.BACKUP_PREFIX}/",
                recursive=True,
                max_files=max_files,
            ):
                if file_version.file_name.endswith(".db"):
                    backups.append(
                        {
                            "name": os.path.basename(file_version.file_name),
                            "path": file_version.file_name,
                            "size": file_version.size,
                            "upload_time": file_version.upload_timestamp,
                            "file_id": file_version.id_,
                        }
                    )

            return sorted(backups, key=lambda x: x["upload_time"], reverse=True)

        except Exception as e:
            logger.error(f"❌ Failed to list backups: {e}")
            return []

    def cleanup_old_backups(self, days: int = None) -> dict:
        """
        Delete backups older than specified retention period.

        Args:
            days: Number of days to retain (uses config default if None).

        Returns:
            Dictionary with cleanup results.
        """
        if days is None:
            days = config.BACKUP_RETENTION_DAYS

        if not self._authorized and not self.api:
            return {"success": False, "error": "Not authorized", "deleted": []}

        try:
            cutoff_date = datetime.now(UTC) - timedelta(days=days)
            deleted = []

            for file_version, _folder in self.bucket.ls(
                folder_to_list=f"{config.BACKUP_PREFIX}/", recursive=True
            ):
                if file_version.upload_timestamp < cutoff_date:
                    self.api.delete_file_version(
                        file_id=file_version.id_,
                        file_name=file_version.file_name,
                    )
                    deleted.append(file_version.file_name)
                    logger.info(f"🗑️ Deleted old backup: {file_version.file_name}")

            return {"success": True, "deleted": deleted, "count": len(deleted)}

        except Exception as e:
            logger.error(f"❌ Failed to cleanup old backups: {e}")
            return {"success": False, "error": str(e), "deleted": []}

    async def download_backup(self, file_id: str, destination: Path) -> bool:
        """
        Download a specific backup file.

        Args:
            file_id: B2 file ID to download.
            destination: Local path to save the file.

        Returns:
            True if successful, False otherwise.
        """
        if not self._authorized and not self.api:
            return False

        try:
            download_dest = B2Api.download_file_by_id(file_id)
            download_dest.save_to(destination)
            logger.info(f"✅ Downloaded backup: {destination}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to download backup: {e}")
            return False

    def get_backup_status(self) -> dict:
        """Get current backup configuration status."""
        return {
            "configured": config.is_backup_configured(),
            "authorized": self._authorized,
            "bucket": config.B2_BUCKET if config.B2_BUCKET else None,
            "data_dir": config.DATA_DIR,
            "retention_days": config.BACKUP_RETENTION_DAYS,
            "interval_minutes": config.BACKUP_INTERVAL_MINUTES,
        }
