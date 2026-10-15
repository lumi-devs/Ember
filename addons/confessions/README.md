# Confessions Cog

Anonymous confession system for Discord servers with support for media uploads, replies, and moderation.

## Features

- Anonymous confession submissions
- Optional titles and media attachments
- Reply system for community engagement
- Report functionality for moderation
- Daily confession limits
- Role-based access control
- Persistent views across bot restarts

## Commands

| Command | Description |
|---------|-------------|
| `/confess` | Submit an anonymous confession |
| `/confess_delete` | Delete a confession by ID (moderator only) |

## Configuration

Required environment variables:

```env
CONFESSION_CHANNEL_ID=<channel_id>      # Where confessions are posted
REPORT_CHANNEL_ID=<channel_id>          # Where reports are sent
MEDIA_CHANNEL_ID=<channel_id>           # For media re-uploads
MOD_CONFESSION_LOGS_CHANNEL_ID=<id>     # Mod logs
DAILY_CONFESSION_LIMIT=3                # Daily submission limit
HOURLY_REPORT_LIMIT=5                   # Hourly report limit
ALLOWED_ROLE_IDS=[123, 456]             # Minimum role IDs for access
```

## File Structure

| File | Purpose |
|------|---------|
| `confess.py` | Main cog with commands and views |
| `helpers.py` | Database utilities and helper functions |
| `cleaner.py` | Message cleanup tasks |
| `confessban.py` | Ban management for confessions |
| `reply.py` | Reply system implementation |

## Database Schema

- `confessions` - Stores confession data
- `reports` - Tracks user reports
- `confessban` - Banned users
- `replies` - Stores reply data
