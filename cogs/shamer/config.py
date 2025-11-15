import discord
from typing import Dict, Any, List

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
    LEADERBOARD_CHANNEL_ID = 1437084755291279371
    SHAME_ROLE_ID = 1437078973220917455
    
    # Hardcoded Blacklists - ADD THESE
    BLACKLISTED_CATEGORY_IDS = [
        1393171103966560307,  # Replace with your category IDs
        1400567851664015491,
        1393171104750764104
    ]
    
    BLACKLISTED_CHANNEL_IDS = [
    ]
    
    # Optional: Whitelist mode (if you want to only track specific channels)
    # WHITELISTED_CHANNEL_IDS = []  # Empty = track all non-blacklisted
    
    # Rotation settings
    ROTATION_INTERVAL_DAYS = 4
    TOP_N_ROLE = 1437084755291279371
    SHAME_CHANNEL_ID = 1437084755291279371