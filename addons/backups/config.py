import os

# B2 Configuration
B2_KEY_ID = os.getenv("B2_KEY_ID")
B2_APP_KEY = os.getenv("B2_APP_KEY")
B2_BUCKET = os.getenv("B2_BUCKET")

# Backup Settings
BACKUP_INTERVAL_MINUTES = int(os.getenv("BACKUP_INTERVAL_MINUTES", "1440"))  # Default: daily
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))  # Default: 30 days
DATA_DIR = os.getenv("DATA_DIR", "data")
BACKUP_PREFIX = os.getenv("BACKUP_PREFIX", "ember-backup")

# Feature flags
ENABLE_SCHEDULED_BACKUP = os.getenv("ENABLE_SCHEDULED_BACKUP", "true").lower() == "true"


def is_backup_configured() -> bool:
    """Check if B2 backup is properly configured."""
    return all([B2_KEY_ID, B2_APP_KEY, B2_BUCKET])
