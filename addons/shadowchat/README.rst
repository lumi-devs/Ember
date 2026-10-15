.. _shadowchat:

ShadowChat
==========

Anonymous gender-matched relay chat roulette with DM-based conversations, optional reveal workflow, and a rating system.

Features
--------

- Anonymous 1-on-1 chat sessions with automatic gender matching
- Private temporary channels created per match
- Relay system using webhooks to mask identities
- Optional identity reveal after session ends
- Date channel creation for matched pairs who reveal
- Session rating system (1-5 stars)
- Reporting and moderation support
- Cooldowns and queue management

Installation
------------

Add this cog to your bot's ``cogs`` directory:

.. code-block:: bash

   cp -r shadowchat /path/to/your/bot/cogs/

Then load the cog in your bot:

.. code-block:: python

   # In your bot's setup or load sequence
   await bot.load_extension("cogs.shadowchat")

Or via the Ember config system if using a config-based loader.

Requirements
------------

- Python 3.11 or higher
- An Ember-based bot (inherits from :class:`core.cog.EmberCog`)
- PostgreSQL or MongoDB database (via Ember core)
- ``discord.py`` (typically provided by the bot)

Configuration
-------------

Configure ShadowChat using the Ember config system:

1. Set the **Entry Channel** where the join card will be posted
2. Set the **Session Category** where temporary session channels are created
3. Set the **Mod Log Channel** for moderation events
4. Set the **Report Channel** where user reports are forwarded

You can configure these via:

- The bot's ``/config`` command (ShadowChat section)
- Environment variables:
  - ``SC_ENTRY_CHANNEL_ID``
  - ``SC_CATEGORY_ID``
  - ``SC_MOD_LOG_CHANNEL_ID``
  - ``SC_REPORT_CHANNEL_ID``

Gender Role Mapping
-------------------

ShadowChat uses role IDs for gender matching. Define these in the bot's configuration under the ``GENDER_ROLES`` constant:

.. code-block:: python

   GENDER_ROLES = {
       "male": 123456789012345678,   # Role ID for male
       "female": 987654321098765432, # Role ID for female
       "nonbinary": 111111111111111111, # Role ID for non-binary
   }

Users must have one of these roles to join the queue.

Commands
--------

User Commands (``/chat``)
~~~~~~~~~~~~~~~~~~~~~~~~~

These commands are used within your session channel or anywhere:

.. list-table::
   :header-rows: 1

   * - Command
     - Description
   * - ``/chat end``
     - End your current ShadowChat session.
   * - ``/chat skip``
     - End and immediately re-queue for a new match.
   * - ``/chat report``
     - Report your partner and end the session.
   * - ``/chat reveal``
     - Request to reveal your identity to your partner.
   * - ``/chat stats``
     - View your ShadowChat statistics (chats, time, messages).
   * - ``/chat help``
     - Show this help message.

Admin Commands (``/sc``)
~~~~~~~~~~~~~~~~~~~~~~~~

These require ``Manage Server`` permissions:

.. list-table::
   :header-rows: 1

   * - Command
     - Description
   * - ``/sc setup``
     - Post the ShadowChat entry card in the configured entry channel.
   * - ``/sc queue``
     - Show the current queue state (who's waiting and their gender).
   * - ``/sc sessions``
     - List all active sessions with user IDs and codenames.
   * - ``/sc end <session_id>``
     - Force-end an active session by its ID (partial match supported).

Session Flow
------------

1. **Joining**: Users with a gender role click ``Find Match`` on the entry card. They enter the queue.
2. **Matching**: When two compatible users (by gender) are found, a private session channel is created. Both users get a DM with an invite.
3. **Chatting**: Messages typed in the session channel are relayed via webhooks to the partner's channel, masking identities. Users are assigned random codenames (e.g., ``NeonFox``, ``CrimsonTiger``).
4. **Ending**: Either user can end the session with ``/chat end``. Both get a rating prompt (1-5 stars). Ratings influence future match quality via a cooldown/penalty system.
5. **Revealing**: After a session ends, users can request to reveal their identity. If both accept, they can choose to:
   - Reveal identities in the current channel
   - Create a persistent ``Date Channel`` for continued conversation
6. **Reporting**: Users can report their partner, which ends the session and applies a cooldown penalty to the reported user.

Rating System
-------------

- After a session ends, both users are prompted to rate the conversation 1-5.
- Skipping ratings applies a 10-session skip (you won't be asked to rate for 10 chats).
- Low ratings may trigger cooldowns or affect match priority.

Moderation
----------

- Reports are logged to the configured report channel.
- Moderators can view active sessions and force-end them via ``/sc`` commands.
- The bot respects Discord's privacy and data deletion requests (GDPR compliance via ``red_delete_data_for_user``).

Data Storage
------------

ShadowChat stores:

- Sessions (user IDs, channel IDs, webhook URLs, timestamps)
- Queue entries (user ID, gender, timestamp)
- Ratings (session ID, rater, rated, score)
- Reveal requests
- User cooldowns and rating skip counters

The database schema is managed by the Ember core framework (PostgreSQL or MongoDB).

License
-------

MIT. See ``LICENSE`` file in the cog directory.
