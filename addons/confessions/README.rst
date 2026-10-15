.. _confessions:

Confessions
===========

Anonymous confession system for Discord servers with support for media uploads, threaded replies, reports, and moderation.

Features
--------

- Anonymous confession submissions via interactive modal
- Optional titles and image attachments
- Threaded reply system for community discussions
- Report functionality for inappropriate content
- Daily confession limits to prevent spam
- Role-based access control (configurable allowed roles)
- Persistent views across bot restarts
- Full confession ban system (ban/unban by user ID, mention, or hash)
- Automatic message cleaning in confession channel
- GDPR compliance (data deletion for users)
- Separate admin and mod log channels with privacy controls

Commands
--------

User Commands
~~~~~~~~~~~~~

There are no traditional slash commands for users. Instead, users interact through the UI:

- **Make a Confession** button: Opens a modal to submit an anonymous confession with optional title, content, and image.
- **Reply** button on each confession: Opens a modal to reply anonymously to the confession thread.
- **Report** button on each confession: Allows users to report problematic content to moderators.

Moderator Commands
~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Command
     - Description
     - Permissions
   * - ``/confession delete <id> [reason]``
     - Delete a confession by ID and renumber all subsequent ones
     - Moderator (via ``@checks.mod()``)
   * - ``/confessban ban <target> [reason]``
     - Ban a user from submitting confessions
     - Administrator
   * - ``/confessban unban <target>``
     - Remove a confession ban from a user
     - Administrator
   * - ``/confessban list``
     - List all banned users (display name, ID, SHA-256 hash)
     - Administrator

Configuration
-------------

Required environment variables or Ember Config fields:

.. code-block:: env

   CONFESSION_CHANNEL_ID=<channel_id>
   # Where confessions are posted. Must be a text channel.

   REPORT_CHANNEL_ID=<channel_id>
   # Where user reports are sent to staff.

   MEDIA_CHANNEL_ID=<channel_id>
   # Channel used to re-host confession images (optional but recommended).

   LOG_CHANNEL_ID=<channel_id>
   # Channel for full confession logs (includes user IDs for admins).

   MOD_LOG_CHANNEL_ID=<channel_id>
   # Channel for mod-facing logs (shows hashed user IDs for privacy).

   DAILY_CONFESSION_LIMIT=3
   # Maximum confessions a user can submit per day.

   HOURLY_REPORT_LIMIT=5
   # Maximum reports a user can send per hour.

   ALLOWED_ROLE_IDS=[123, 456]
   # List of role IDs that are allowed to submit confessions (optional, empty means everyone).

Database Schema
---------------

The cog uses the following SQLite tables (managed by ``ConfessionsService``):

- ``confessions``: Stores confession data (id, user_id, title, description, image_url, message_id, created_at)
- ``replies``: Stores reply data with threading support (id, confession_id, parent_reply_id, user_id, description, image_url, message_id, timestamp)
- ``reports``: Tracks user reports (id, confession_id, user_id, reason, reported_at, resolved_by, resolved_at, action_taken)
- ``confessbans``: Stores banned users (user_id, reason, banned_by, banned_at)
- ``confession_views``: Tracks daily confession counts per user for rate limiting

Installation
------------

This cog is designed to be installed into an Ember bot instance via the built-in downloader.

Place the ``confessions`` directory (containing this ``README.rst``) into your ``cogs/`` folder, then run:

.. code-block:: bash

   [p]cog install confessions /path/to/confessions

Or if the cog is available in a remote repository (AAA3A-cogs style), use:

.. code-block:: bash

   [p]cog install confessions

After installation, reload the cog:

.. code-block:: bash

   [p]reload confessions

Finally, configure the required channels using the Ember Config system or environment variables.

Notes
-----

- The bot must have ``Send Messages``, ``Create Public Threads``, ``Attach Files``, and ``Embed Links`` permissions in the confession channel.
- Users submit confessions anonymously; their identity is never revealed in public logs. Only admins with access to the ``LOG_CHANNEL_ID`` can see hashed user IDs.
- The ``Make a Confession`` button is only visible on the latest confession to encourage engagement; older confessions hide this button.
- The cleaner cog automatically deletes non-bot messages posted in the confession channel to maintain a clean feed of confession cards.

File Structure
--------------

.. list-table::
   :header-rows: 1

   * - File
     - Purpose
   * - ``cog.py``
     - Main cog with ``Confessions`` class, confession deletion command, and persistent view loading
   * - ``confessban.py``
     - ``ConfessBan`` cog for banning management
   * - ``cleaner.py``
     - ``ConfessionCleaner`` cog that deletes stray messages in the confession channel
   * - ``database.py``
     - Database initialization and low-level queries
   * - ``services/confessions_service.py``
     - Business logic layer; all database operations
   * - ``views.py``
     - UI components: ``ConfessionLayoutView`` and builder
   * - ``reply.py``
     - Reply system UI: ``ReplyLayoutView`` and reply interactions
   * - ``modals.py``
     - Modals for confession and reply submission
   * - ``helpers.py``
     - Utility functions (SHA-256 hashing, service setter)
   * - ``config.py``
     - Not used; configuration is handled by ``core.config``

License
-------

This cog is part of the Ember Discord bot project. See the ``LICENSE`` file in the project root.
