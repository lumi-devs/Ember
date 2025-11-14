import discord
from typing import Dict, Any

class ShameConfig:
    # System Configuration
    DB_PATH = "data/shame.db"
    MAX_POINTS_PER_HOUR = 300
    MAX_DECAY_PER_HOUR = 10
    DECAY_PERCENTAGE = 0.10
    
    # Timing Configuration
    LEADERBOARD_INTERVAL_DAYS = 4
    ROLE_ROLL_INTERVAL_DAYS = 4
    
    # Channel and Role IDs (Set these in your server)
    LEADERBOARD_CHANNEL_ID = None  # Set your channel ID
    SHAME_ROLE_ID = None  # Single role ID for top 5 users
    
    # Rotation settings (for compatibility with your rotation code)
    ROTATION_INTERVAL_DAYS = 4
    TOP_N_ROLE = None  # This should be the same as SHAME_ROLE_ID
    SHAME_CHANNEL_ID = None  # This should be the same as LEADERBOARD_CHANNEL_ID
    
    # No need for admin roles list - using administrator permission check