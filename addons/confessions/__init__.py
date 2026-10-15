"""Confessions - Anonymous confession system cog.

This package provides three cogs:
- Confessions: Main confession system with anonymous submissions, threaded replies, and moderation tools
- ConfessBan: Ban management for preventing users from submitting confessions
- ConfessionCleaner: Automatically cleans non-bot messages from the confession channel
"""

from .cog import Confessions, setup
from .confessban import ConfessBan
from .cleaner import ConfessionCleaner

__all__ = ["Confessions", "ConfessBan", "ConfessionCleaner", "setup"]
