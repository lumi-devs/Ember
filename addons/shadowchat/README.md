# ShadowChat Cog

Anonymous 1:1 chat system for Discord servers. Allows users to be paired for anonymous conversations.

## Features

- Anonymous 1:1 messaging
- Session pairing system
- Queue management
- Inactivity monitoring
- Custom session views
- Relay handler for message forwarding

## Commands

| Command | Description |
|---------|-------------|
| `/shadowchat start` | Start finding a chat partner |
| `/shadowchat stop` | Stop current session |
| `/shadowchat report` | Report a user |

## File Structure

| File | Purpose |
|------|---------|
| `shadowchat.py` | Main cog with session commands |
| `constants.py` | Configuration constants |
| `inactivity_watcher.py` | Monitor inactive sessions |
| `models.py` | Data models for sessions |
| `queue_manager.py` | User queue management |
| `relay_handler.py` | Message relay between users |
| `session_manager.py` | Session lifecycle management |
| `views.py` | Discord UI components |

## Configuration

Required environment variables:
```env
SHADOWCHAT_ENTRY_CHANNEL_ID=<channel_id>
SHADOWCHAT_DB_PATH=data/shadowchat.db
```

## Features

### Session Management
- Automatic user pairing
- Session timeout handling
- Graceful session termination

### Queue System
- FIFO queue for finding partners
- Queue position tracking
- Auto-removal of inactive users

### Inactivity Monitoring
- Detect inactive sessions
- Auto-end stale sessions
- Notify users of inactivity

### Message Relay
- Secure message forwarding
- User identity protection
- Message formatting preservation
