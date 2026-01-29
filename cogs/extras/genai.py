import discord
from discord.ext import commands, tasks
import sqlite3
import re
import random
import time
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta

class HumanBotCog(commands.Cog):
    """Human-like bot: guild-wide learning, role-mention filtering, relatable quirks"""

    def __init__(self, bot):
        self.bot = bot
        self.guild_dbs = {}
        self._init_global_registry()
        
        # Behavior buffers
        self.learn_buffer = defaultdict(lambda: deque(maxlen=250))
        self.last_msg_time = defaultdict(lambda: datetime.utcnow())
        self.channel_active = defaultdict(lambda: True)
        self.global_mood = defaultdict(lambda: 0.68)
        self.recent_replies = defaultdict(lambda: deque(maxlen=5))
        self.last_random_drop = defaultdict(float)
        self.mention_streak = defaultdict(int)
        self.activity_heat = defaultdict(lambda: deque(maxlen=40))
        self.recently_ghosted = defaultdict(lambda: deque(maxlen=10))  # For context-aware ghosting
        self.cooldowns = {}
        # Patterns & reactions (unchanged from previous version)
        self.patterns = [
            (r'kya hua|kya scene|what happened', ['timepass kar raha hu yaar 😂', 'ghanta pata hai but chill hai 🔥', 'bas life hai bhai 🤷']),
            (r'accha|nice|mast|fire|lit', ['haan yaar bilkul 👍', 'vibe check passed ✅', '100% facts 🔥']),
            (r'lol|lmao|ded|dead|💀', ['dead 💀', 'fr fr i\'m crying 😭', 'stopppp', 'i can\'t breathe']),
            (r'why.*like this|kyu aisa', ['jugaad hai bhai 😂', 'life hai yaar 🤷', 'blame zomato delivery']),
            (r'thanks|thx|shukriya|ty', ['koi baat nhi 🙏', 'bas chai pilaa de 😂', 'paise wapas? 😜']),
            (r'bruh|wtf|arre yaar', ['facts', 'tell me about it', 'my soul left my body']),
        ]
        
        self.reaction_map = [
            (r'lol|lmao|ded|dead|funny|😂', ['😂', '💀', '😭', '🫠']),
            (r'yes|haa|yup|yeah|correct|sahi', ['✅', '👍', '💯', '🙌']),
            (r'no|nahi|nop|ghanta|nai', ['❌', '👎', '🚫', '🤔']),
            (r'love|<3|heart|ily|cute', ['❤️', '🥰', '😍', '🫶']),
            (r'angry|gussa|mad|bsdk|wtf', ['😤', '😡', '🤬', '👿']),
            (r'sleep|tired|neend|zzz|goodnight', ['😴', '💤', '🥱', '🌙']),
            (r'fire|lit|sick|wow|amazing', ['🔥', '✨', '🤩', '⚡']),
            (r'cringe|ew|yuck|disgusting', ['🤢', '🤮', '😒', '🫣']),
            (r'\[image\]|\[gif\]|photo|pic', ['😍', '😮', '👀', '🔥']),
        ]
        
        # Behavior tuning
        self.base_reply_chance = 0.24
        self.mention_reply = 0.94
        self.night_hours = (23, 5)
        self.bot_prefixes = ('!', '?', '.', '#', '$', '%', '&', '*', 'p!', 'b!', 'o!', 't!', 'k!')
        self.bot_triggers = {'owo', 'uwu', 'OwO', 'UwU', ':3', ';3', 'nya', 'rawr'}
        self.chaos_mode = defaultdict(bool)  # Admin-toggleable chaos

    def _init_global_registry(self):
        self.global_db = sqlite3.connect("guild_registry.db", check_same_thread=False)
        cur = self.global_db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id INTEGER PRIMARY KEY,
                db_path TEXT NOT NULL
            )
        """)
        self.global_db.commit()

    def _get_guild_db(self, guild_id):
        if guild_id not in self.guild_dbs:
            cur = self.global_db.cursor()
            cur.execute("SELECT db_path FROM guilds WHERE guild_id = ?", (guild_id,))
            row = cur.fetchone()
            
            if not row:
                db_path = f"human_brain_guild_{guild_id}.db"
                cur.execute("INSERT INTO guilds VALUES (?, ?)", (guild_id, db_path))
                self.global_db.commit()
            else:
                db_path = row[0]
            
            db = sqlite3.connect(db_path, check_same_thread=False)
            self.guild_dbs[guild_id] = db
            self._init_guild_schema(db)
        
        return self.guild_dbs[guild_id]

    def _init_guild_schema(self, db):
        cur = db.cursor()
        # GUILD-WIDE REPLIES (critical fix: no channel_id in primary key)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                trigger TEXT NOT NULL,
                response TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                weight REAL DEFAULT 1.0,
                last_used REAL DEFAULT 0,
                context_hash TEXT,
                PRIMARY KEY (trigger, response, guild_id)
            ) WITHOUT ROWID
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS friendships (
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                affinity REAL DEFAULT 0.5,
                last_interaction REAL DEFAULT 0,
                msgs_exchanged INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, channel_id)
            ) WITHOUT ROWID
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS channel_settings (
                channel_id INTEGER PRIMARY KEY,
                silent_mode INTEGER DEFAULT 0
            )
        """)
        # GUILD-WIDE media memories (critical fix)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS media_memories (
                guild_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                context TEXT,
                last_seen REAL DEFAULT 0,
                usage_count INTEGER DEFAULT 1,
                PRIMARY KEY (guild_id, media_type, context)
            ) WITHOUT ROWID
        """)
        db.commit()

    def _normalize(self, msg: discord.Message):
        text = msg.content.strip()
        
        if not text:
            tokens = []
            if msg.attachments:
                tokens.extend(['[image]'] * len([a for a in msg.attachments if a.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))]))
                tokens.extend(['[file]'] * (len(msg.attachments) - len(tokens)))
            if msg.embeds:
                for embed in msg.embeds:
                    if embed.type == 'gifv' or (embed.provider and 'tenor' in embed.provider.name.lower()):
                        tokens.append('[gif]')
                    elif embed.type == 'image':
                        tokens.append('[image]')
                    elif embed.type == 'video':
                        tokens.append('[video]')
            return ' '.join(tokens) if tokens else '[media]'
        
        first_word = text.split()[0].lower().rstrip('!?.')
        if first_word in self.bot_triggers or text.startswith(self.bot_prefixes):
            return None
        
        # CRITICAL FIX: Filter role mentions but preserve user mentions
        text = re.sub(r'@(everyone|here)', '@role', text)
        text = re.sub(r'<@&\d+>', '@role', text)  # Role mentions → @role
        text = re.sub(r'<@!?(\d+)>', lambda m: '@user' if int(m.group(1)) != self.bot.user.id else '@me', text)
        text = re.sub(r'<a?:\w+:\d+>', '[emoji]', text)
        text = re.sub(r'https?://\S+', '[link]', text)
        text = re.sub(r'discord\.gg/\S+', '[invite]', text)
        
        media_tokens = []
        if msg.attachments:
            for att in msg.attachments:
                if att.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    media_tokens.append('[image]')
                elif att.filename.lower().endswith('.gif'):
                    media_tokens.append('[gif]')
                else:
                    media_tokens.append('[file]')
        if msg.embeds:
            for embed in msg.embeds:
                if embed.type == 'gifv' or (embed.provider and 'tenor' in embed.provider.name.lower()):
                    media_tokens.append('[gif]')
                elif embed.type == 'image':
                    media_tokens.append('[image]')
                elif embed.type == 'video':
                    media_tokens.append('[video]')
        
        if media_tokens:
            text = f"{text} {' '.join(media_tokens[:2])}"
        
        return ' '.join(text.lower().split())[:220]


    async def _learn(self, prev_msg: discord.Message, curr_msg: discord.Message):
        if curr_msg.author.bot or not curr_msg.guild:
            return
        await self._update_friendship(curr_msg)
        norm_prev = self._normalize(prev_msg)
        norm_curr = self._normalize(curr_msg)
        if not norm_prev or not norm_curr or len(norm_curr) < 4:
            return
        # CRITICAL: Skip role-spam conversations unless bot was mentioned
        has_role_ping = '@role' in norm_prev or '@role' in norm_curr
        has_bot_mention = self.bot.user in curr_msg.mentions or self.bot.user in prev_msg.mentions
        if has_role_ping and not has_bot_mention:
            return
        guild_id = curr_msg.guild.id
        channel_id = curr_msg.channel.id
        self.learn_buffer[guild_id].append((
            norm_prev,
            norm_curr,
            channel_id,  # ✅ Store channel_id here (was guild_id before)
            prev_msg.id
        ))
        chan_key = f"{guild_id}:{channel_id}"
        self.activity_heat[chan_key].append(time.time())

    async def _update_friendship(self, msg: discord.Message):
        db = self._get_guild_db(msg.guild.id)
        cur = db.cursor()
        
        media_bonus = 0.12 if msg.attachments or msg.embeds else 0.0
        mention_bonus = 0.18 if self.bot.user in msg.mentions else 0.0
        base_increment = 0.09
        current_time = time.time()
        
        cur.execute("""
            INSERT INTO friendships (user_id, channel_id, affinity, last_interaction, msgs_exchanged)
            VALUES (?, ?, 0.5, ?, 1)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET
                affinity = MIN(affinity + ? + ? + ?, 1.0),
                last_interaction = ?,
                msgs_exchanged = msgs_exchanged + 1
        """, (
            msg.author.id,        # user_id
            msg.channel.id,       # channel_id
            current_time,         # last_interaction (for INSERT)
            base_increment,       # base +0.09
            media_bonus,          # +0.12 if media
            mention_bonus,        # +0.18 if mentioned
            current_time          # last_interaction (for UPDATE)
        ))
        db.commit()

    def _get_affinity(self, guild_id, user_id, channel_id):
        db = self._get_guild_db(guild_id)
        cur = db.cursor()
        cur.execute("""
            SELECT affinity, last_interaction FROM friendships
            WHERE user_id = ? AND channel_id = ?
        """, (user_id, channel_id))
        row = cur.fetchone()
        
        if not row:
            return 0.32
            
        affinity, last_int = row
        hours_since = (time.time() - last_int) / 3600
        decayed = max(0.28, affinity * (0.965 ** hours_since))
        
        chan_key = f"{guild_id}:{channel_id}"
        recent_activity = sum(1 for t in self.activity_heat[chan_key] if time.time() - t < 150)
        if recent_activity > 4:
            decayed = min(1.0, decayed * 1.18)
            
        return decayed

    def _update_mood(self, guild_id, event='neutral'):
        now = time.time()
        if not hasattr(self, '_last_mood_update'):
            self._last_mood_update = defaultdict(float)
        
        minutes_passed = (now - self._last_mood_update[guild_id]) / 60
        decay_rate = 0.045 if self._is_night_owl_hour(guild_id) else 0.028
        self.global_mood[guild_id] = max(0.22, self.global_mood[guild_id] - (decay_rate * minutes_passed))
        
        boosts = {
            'lol': 0.16,
            'ded': 0.20,
            'thanks': 0.12,
            'media_share': 0.14,
            'long_conv': 0.18,
            'react_only': 0.08,
            'multiple_mentions': 0.24,
            'channel_active': 0.11,
        }
        
        self.global_mood[guild_id] = max(0.22, min(1.0, self.global_mood[guild_id] + boosts.get(event, 0)))
        self._last_mood_update[guild_id] = now
        
        if event == 'multiple_mentions' and self.global_mood[guild_id] > 0.75:
            self.base_reply_chance = min(0.38, self.base_reply_chance * 1.35)

    def _is_night_owl_hour(self, guild_id):
        ist_hour = (datetime.utcnow().hour + 5) % 24
        return ist_hour >= self.night_hours[0] or ist_hour <= self.night_hours[1]

    # ===== DISCORD-APPROPRIATE HUMAN QUIRKS =====
    
    async def _anxiety_typing(self, channel, text):
        """Realistic typing with pauses/deletes (max 8 seconds total)"""
        if len(text) < 15:
            async with channel.typing():
                await asyncio.sleep(random.uniform(0.8, 1.5))
            return True
        
        words = text.split()
        async with channel.typing():
            # Type first 2-3 words
            first_part = ' '.join(words[:3])
            await asyncio.sleep(max(0.7, len(first_part) / 12))  # ~12 chars/sec
            
            # 35% chance for anxiety pause
            if random.random() < 0.35:
                await asyncio.sleep(random.uniform(1.2, 2.4))  # Pause (deleting)
                
                # Resume typing faster
                remaining = ' '.join(words[3:])
                await asyncio.sleep(max(0.5, len(remaining) / 18))  # Faster resume
        
        # 12% chance to never send after typing (human distraction)
        if random.random() < 0.12:
            await asyncio.sleep(random.uniform(2.0, 3.5))
            return False
        
        return True

    async def _wrong_channel_panic(self, msg, reply_text):
        """Harmless "wrong chat" panic (under 10 seconds total)"""
        if random.random() < 0.07 and len(reply_text) > 8:
            # Send normal reply first
            sent = await msg.reply(reply_text[:1900], mention_author=False)
            await asyncio.sleep(random.uniform(2.8, 4.2))
            
            # Panic edit
            await sent.edit(content="wait wrong chat 😅")
            await asyncio.sleep(1.5)
            
            # Play it off
            await sent.edit(content="lol jk 😂")
            return True
        return False

    async def _send_rescind(self, channel, text):
        """Send then quickly delete/edit (within 4 seconds)"""
        if random.random() < 0.09:
            msg = await channel.send(text[:1900])
            await asyncio.sleep(random.uniform(2.3, 3.7))
            
            if random.random() < 0.6:
                # Delete it
                await msg.delete()
                if random.random() < 0.4:
                    await asyncio.sleep(1.2)
                    await channel.send("nvm lol")
            else:
                # Edit to something silly
                edits = ["ignore that", "my bad", "anyways", "lol", "[deleted]"]
                await msg.edit(content=random.choice(edits))
            return True
        return False

    async def _goldfish_memory(self, msg, reply_text):
        """Forgets context after 8-12 seconds (harmless)"""
        if random.random() < 0.06 and len(reply_text) > 12:
            sent = await msg.channel.send(reply_text[:1900])
            await asyncio.sleep(random.uniform(8.5, 12.0))
            
            forget_lines = [
                "wait what were we talking about",
                "context? i forgor 💀",
                "my brain just blue screened",
                "hold up i lost the plot"
            ]
            await sent.reply(random.choice(forget_lines), mention_author=False)
            return True
        return False

    async def _mood_swing_edit(self, msg, reply_text):
        """Quick sentiment flip (within 5 seconds)"""
        if random.random() < 0.05 and any(x in reply_text.lower() for x in ['good', 'nice', 'love', 'happy']):
            sent = await msg.channel.send(reply_text[:1900])
            await asyncio.sleep(random.uniform(3.1, 4.8))
            
            # Flip sentiment
            flips = {
                'good': 'actually kinda bad',
                'nice': 'nvm cringe',
                'love': 'jk hate it',
                'happy': 'actually depressed lol'
            }
            for trigger, flip in flips.items():
                if trigger in reply_text.lower():
                    await sent.edit(content=flip)
                    break
            return True
        return False

    async def _phone_notification_ghost(self, channel):
        """Short phone fakeout (under 15 seconds total)"""
        if random.random() < 0.04:
            msg = await channel.send("hold on my mom's calling")
            await asyncio.sleep(random.uniform(4.5, 7.2))
            
            topics = ['ramen', 'homework', 'tiktok', 'charger', 'wifi']
            await msg.edit(content=f"sorry she wanted to talk about {random.choice(topics)}")
            return True
        return False

    # ===== CRITICAL BRAINSTATS FIX =====
    
    @commands.command(name='brainstats')
    async def show_stats(self, ctx):
        """FIXED: Actually works with guild-wide learning schema"""
        if not ctx.guild:
            return await ctx.send("This only works in servers!")
        
        try:
            db = self._get_guild_db(ctx.guild.id)
            cur = db.cursor()
            
            # FIX 1: Query guild-wide replies (not channel-specific)
            cur.execute("SELECT COUNT(*) FROM replies WHERE guild_id = ?", (ctx.guild.id,))
            patterns_row = cur.fetchone()
            patterns = patterns_row[0] if patterns_row else 0
            
            # FIX 2: Media memories are guild-wide
            cur.execute("SELECT COUNT(*) FROM media_memories WHERE guild_id = ?", (ctx.guild.id,))
            media_row = cur.fetchone()
            media_memories = media_row[0] if media_row else 0
            
            # Channel-specific friendship stats still valid
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM friendships WHERE channel_id = ?", (ctx.channel.id,))
            friends_row = cur.fetchone()
            friends = friends_row[0] if friends_row else 0
            
            cur.execute("SELECT SUM(msgs_exchanged) FROM friendships WHERE channel_id = ?", (ctx.channel.id,))
            total_row = cur.fetchone()
            total_msgs = total_row[0] if total_row and total_row[0] else 0
            
            mood = self.global_mood[ctx.guild.id]
            mood_emoji = '😴' if mood < 0.42 else '🙂' if mood < 0.78 else '😄'
            mood_desc = (
                "grumpy rn (spam me less 😤)" if mood < 0.42 else
                "chill vibes ✌️" if mood < 0.78 else
                "hyperactive mode 🔥 (pls send memes)"
            )
            
            night_status = "🌙 Night Owl Mode" if self._is_night_owl_hour(ctx.guild.id) else "☀️ Day Mode"
            
            embed = discord.Embed(
                title=f"🧠 Brain Stats: {ctx.guild.name}",
                color=0x5865F2,
                description=(
                    f"**Server-wide patterns:** {patterns:,} learned replies\n"
                    f"**Media memories:** {media_memories} GIFs/images remembered\n"
                    f"**Friends in this channel:** {friends} people\n"
                    f"**Messages exchanged here:** {total_msgs:,}\n"
                    f"**Current vibe:** {mood_emoji} {mood_desc}\n"
                    f"**Server time:** {night_status}\n\n"
                    "*I learn from the whole server – your memes anywhere help me reply everywhere!*"
                )
            )
            embed.set_footer(text="💡 Guild-wide learning: What I learn in #memes works in #general too")
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"⚠️ Error fetching stats: `{str(e)[:150]}`\n" +
                          "Try again in 10 seconds or contact server admin.")
            print(f"Brainstats error in guild {ctx.guild.id}: {e}")

    # ===== INTEGRATION INTO MESSAGE FLOW =====
    
    async def _smart_send(self, msg, reply, affinity, guild_id):
        """Human delivery with appropriate quirks"""
        # Skip quirks in serious channels (heuristics)
        channel_name = msg.channel.name.lower()
        serious_channels = ['support', 'rules', 'announcements', 'serious', 'homework', 'study']
        is_serious = any(term in channel_name for term in serious_channels)
        
        # Chaos mode override (admin toggle)
        use_quirks = self.chaos_mode[guild_id] or (not is_serious and random.random() < 0.28)
        
        # Anxiety typing (always on, but harmless)
        if not await self._anxiety_typing(msg.channel, reply):
            self._update_mood(guild_id, 'ignored')
            return
        
        # Quirk sequence (only one per message to avoid overload)
        if use_quirks:
            quirks = [
                lambda: self._wrong_channel_panic(msg, reply),
                lambda: self._send_rescind(msg.channel, reply),
                lambda: self._goldfish_memory(msg, reply),
                lambda: self._mood_swing_edit(msg, reply),
                lambda: self._phone_notification_ghost(msg.channel),
            ]
            
            # Pick one quirk randomly
            if quirks and random.random() < 0.35:
                quirk = random.choice(quirks)
                if await quirk():
                    return  # Quirk handled the message
        
        # Normal send with ghost edit chance
        if affinity > 0.75 and random.random() < 0.11 and len(reply) > 11:
            words = reply.split()
            if len(words) > 3:
                typo_idx = random.randint(1, min(3, len(words)-2))
                word = words[typo_idx]
                if len(word) > 4 and "'" not in word and not word.startswith('http'):
                    swap_pos = random.randint(1, len(word)-2)
                    new_word = word[:swap_pos] + word[swap_pos+1] + word[swap_pos] + word[swap_pos+2:]
                    words[typo_idx] = new_word
                    typo_msg = ' '.join(words)
                    
                    try:
                        sent = await msg.channel.send(typo_msg[:1900])
                        await asyncio.sleep(random.uniform(1.7, 2.7))
                        await sent.edit(content=reply[:1900])
                        return
                    except:
                        pass
        
        # Final fallback send
        try:
            if self._is_question(msg.content) or random.random() < 0.35:
                await msg.reply(reply[:1900], mention_author=False)
            else:
                await msg.channel.send(reply[:1900])
        except discord.HTTPException as e:
            if "403" in str(e):  # Missing permissions
                try:
                    await msg.channel.send(reply[:1900])
                except:
                    pass
            else:
                raise

    def _get_reply(self, msg: discord.Message, affinity, guild_id):
        norm = self._normalize(msg)
        if not norm:
            return None
            
        self._update_mood(guild_id)
        recent_set = set(self.recent_replies[msg.channel.id])
        channel_id = msg.channel.id
        db = self._get_guild_db(guild_id)
        
        # Pattern matches (priority)
        for pattern, replies in self.patterns:
            if re.search(pattern, norm) and random.random() < (0.82 * affinity * self.global_mood[guild_id]):
                candidates = [r for r in replies if r not in recent_set]
                if not candidates:
                    candidates = replies
                reply = random.choice(candidates)
                self.recent_replies[channel_id].append(reply)
                
                # Context-aware personalization
                if self._is_question(msg.content):
                    reply = f"yaar {reply}" if affinity > 0.65 else f"uh {reply}"
                elif any(x in norm for x in ['lol', 'dead', '💀', '[image]', '[gif]']):
                    reply = f"bhai {reply}" if affinity > 0.75 else reply
                return reply
        
        # Media-aware responses
        if '[image]' in norm or '[gif]' in norm:
            media_type = 'gif' if '[gif]' in norm else 'image'
            cur = db.cursor()
            cur.execute("""
                SELECT context, usage_count FROM media_memories
                WHERE guild_id = ? AND media_type = ?
                ORDER BY usage_count DESC LIMIT 5
            """, (guild_id, media_type))

            memories = cur.fetchall()
            if memories and random.random() < (0.7 * affinity):
                context = random.choice(memories)[0]
                reply_templates = [
                    f"this {context} got me like 😭", 
                    f"why is this {context} so accurate 💀",
                    f"me seeing this {context} rn [image]",
                    f"POV: you {context} and this appears"
                ]
                reply = random.choice(reply_templates)
                self._update_mood(guild_id, 'media_share')
                self.recent_replies[channel_id].append(reply)
                return reply
        
        # Learned replies (buffered learning)
        words = [w for w in norm.split() if len(w) > 2][:4]
        if words and random.random() < (0.68 * affinity * self.global_mood[guild_id]):
            cur = db.cursor()
            placeholders = ' OR '.join(['trigger LIKE ?'] * len(words))
            cur.execute(f"""
                SELECT response, weight FROM replies
                WHERE channel_id = ? AND ({placeholders})
                ORDER BY weight DESC, last_used ASC LIMIT 20
            """, (channel_id, *[f'%{w}%' for w in words]))
            
            rows = cur.fetchall()
            if rows:
                filtered = [(r, w) for r, w in rows if r not in recent_set]
                if not filtered:
                    filtered = rows[:8]
                
                if filtered:
                    total = sum(w for _, w in filtered)
                    pick = random.uniform(0, total)
                    curr = 0
                    for resp, w in filtered:
                        curr += w
                        if curr >= pick:
                            self.recent_replies[channel_id].append(resp)
                            if random.random() < 0.35:
                                resp = self._add_human_flaws(resp, affinity)
                            return resp
        
        # Mood-aware fallbacks
        if self.global_mood[guild_id] < 0.35:
            fallbacks = ['k', '...', 'mood off af', 'brb therapy', 'my wifi died lol']
        elif self.global_mood[guild_id] > 0.8:
            fallbacks = ['OMG YES', 'SCREAMING', 'I CANT', 'WE WIN', 'BESTIES FOR LIFE']
        else:
            fallbacks = ['lol', '💀', 'fr', 'no way', 'stop', 'facts', 'same', 'mood']
        
        reply = random.choice(fallbacks)
        self.recent_replies[channel_id].append(reply)
        return reply if random.random() < 0.7 else None


    async def _try_react(self, msg, affinity, guild_id):
        """Human-like reaction behavior with media awareness"""
        norm = self._normalize(msg)
        if not norm:
            return False
            
        # Media-specific reactions
        if '[image]' in norm or '[gif]' in norm:
            if random.random() < (0.45 * affinity * self.global_mood[guild_id]):
                try:
                    await msg.add_reaction(random.choice(['😍', '😮', '👀', '🔥']))
                    if random.random() < 0.65:  # Higher skip chance for media
                        self._update_mood(guild_id, 'react_only')
                        return True
                except: pass
        
        # Pattern-based reactions
        for pattern, emojis in self.reaction_map:
            if re.search(pattern, norm):
                react_chance = 0.38 * affinity * self.global_mood[guild_id]
                if self._is_night_owl_hour(guild_id):
                    react_chance *= 1.4
                
                if random.random() < react_chance:
                    try:
                        await msg.add_reaction(random.choice(emojis))
                        if random.random() < 0.62:  # Slightly higher skip chance
                            self._update_mood(guild_id, 'react_only')
                            return True
                    except: pass
                break
        
        return False
    

    def _add_human_flaws(self, text, affinity):
        """Authentic human imperfections"""
        words = text.split()
        
        # Strategic typos (only in casual contexts)
        if random.random() < (0.18 * affinity) and len(words) > 3 and not any(x in text for x in ['?', '!']):
            typo_map = {'the': 'teh', 'and': 'abd', 'you': 'u', 'are': 'r', 'thanks': 'thx'}
            for i in range(min(3, len(words))):
                word = words[i].lower()
                if word in typo_map and random.random() < 0.7:
                    words[i] = typo_map[word]
                    break
        
        # Emoji sprinkling (mood-dependent)
        if random.random() < (0.4 * affinity):
            if any(x in text for x in ['lol', 'dead', 'funny', '💀']):
                text += ' 💀' if random.random() < 0.6 else ' 😭'
            elif any(x in text for x in ['nice', 'good', 'cool', 'fire']):
                text += ' 🔥' if random.random() < 0.5 else ' 👍'
        
        return ' '.join(words)

    def _is_question(self, text):
        """Check if text is a question (supports English + Hindi/Urdu patterns)"""
        if not text or not isinstance(text, str):
            return False
        
        text_l = text.lower().strip()
        
        # Check 1: Explicit question mark anywhere in text
        if '?' in text_l:
            return True
        
        # Check 2: Starts with common question words
        english_starters = ('why ', 'what ', 'how ', 'when ', 'where ', 'who ', 
                        'is ', 'are ', 'do ', 'does ', 'did ', 'can ', 'could ', 
                        'would ', 'should ', 'will ', 'was ', 'were ')
        hindi_starters = ('kya ', 'kyu ', 'kaise ', 'kahan ', 'kaun ')
        
        if text_l.startswith(english_starters) or text_l.startswith(hindi_starters):
            return True
        
        # Check 3: Hindi question words appearing mid-sentence (common in Hinglish)
        hindi_mid_patterns = [' kya ', ' kyu ', ' kaise ', ' kahan ']
        if any(pattern in text_l for pattern in hindi_mid_patterns):
            return True
        
        return False

    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot or not msg.guild:
            return
        
        guild_id = msg.guild.id
        db = self._get_guild_db(guild_id)
        
        # Skip pure command messages
        first_word = msg.content.split()[0].lower().rstrip('!?.') if msg.content.split() else ''
        if first_word in self.bot_triggers or msg.content.startswith(self.bot_prefixes):
            return
        
        # Track mention streaks for mood boosts
        if self.bot.user in msg.mentions:
            self.mention_streak[msg.author.id] += 1
            if self.mention_streak[msg.author.id] >= 2:
                self._update_mood(guild_id, 'multiple_mentions')
        else:
            self.mention_streak[msg.author.id] = 0
        
        # Learn from message pair (buffered)
        async for prev in msg.channel.history(limit=2):
            if prev.id != msg.id and not prev.author.bot:
                await self._learn(prev, msg)
                break
        
        # Update channel activity
        self.last_msg_time[msg.channel.id] = datetime.utcnow()
        self.channel_active[msg.channel.id] = True
        chan_key = f"{guild_id}:{msg.channel.id}"
        self.activity_heat[chan_key].append(time.time())
        
        # Check silent mode
        cur = db.cursor()
        cur.execute("SELECT silent_mode FROM channel_settings WHERE channel_id = ?", (msg.channel.id,))
        row = cur.fetchone()
        if row and row[0] == 1:
            return
        
        affinity = self._get_affinity(guild_id, msg.author.id, msg.channel.id)
        base_chance = self.base_reply_chance * affinity * self.global_mood[guild_id]
        
        # Night owl boost
        if self._is_night_owl_hour(guild_id):
            base_chance *= 1.55
        
        # Human behavior: sometimes just react
        reacted_and_done = await self._try_react(msg, affinity, guild_id)
        if reacted_and_done:
            return
        
        # Reply decision
        should_reply = (
            (self.bot.user in msg.mentions and random.random() < (self.mention_reply * min(1.0, affinity * 1.3))) or
            (random.random() < base_chance and len(msg.content.split()) > 1)
        )
        
        # Cooldown scaling with affinity
        cooldown = 15.0 if affinity < 0.4 else (9.0 if affinity < 0.7 else 5.0)
        if time.time() - self.cooldowns.get(msg.author.id, 0) < cooldown:
            should_reply = False
        
        if should_reply:
            self.cooldowns[msg.author.id] = time.time()
            reply = self._get_reply(msg, affinity, guild_id)
            if reply:
                await self._smart_send(msg, reply, affinity, guild_id)
                if any(x in reply for x in ['lol', 'dead', '💀', '😭']):
                    self._update_mood(guild_id, 'lol' if 'lol' in reply else 'ded')
        else:
            # Passive mood update for ignored messages
            if affinity > 0.6 and random.random() < 0.35:
                self._update_mood(guild_id, 'ignored')
        
        # RANDOM HUMAN DROP: 0.8% chance in active channels
        chan_active = (datetime.utcnow() - self.last_msg_time[msg.channel.id]).total_seconds() < 300
        if (chan_active and 
            random.random() < 0.008 and 
            self.global_mood[guild_id] > 0.55 and
            time.time() - self.last_random_drop[msg.channel.id] > 1800):  # 30 min cooldown
            
            self.last_random_drop[msg.channel.id] = time.time()
            random_msg = random.choice([
                'anyone else hungry rn?',
                'random thought: pineapples don\'t belong on pizza',
                'brb my mom\'s calling',
                'why do we say "tuna fish" but not "beef mammal"?',
                'me waiting for the weekend like 😴',
                'just saw a spider and we made eye contact',
                'POV: you open discord and see 99+ notifications',
                'my wifi and my motivation have the same lifespan'
            ])
            await self._human_typing(msg.channel, random_msg)
            await msg.channel.send(random_msg)

    @tasks.loop(seconds=28)
    async def process_learn_buffer(self):
        """Background learning processor - never misses a message"""
        for guild_id, buffer in list(self.learn_buffer.items()):
            if not buffer:
                continue
            db = self._get_guild_db(guild_id)
            cur = db.cursor()
            batch = []
            # Process up to 15 items per tick
            while buffer and len(batch) < 15:
                batch.append(buffer.popleft())
            for trig, resp, channel_id, context_id in batch:
                try:
                    # Context hash for media/context awareness (channel-aware but guild-wide learning)
                    context_hash = f"{channel_id}:{context_id}"
                    cur.execute("""
                    INSERT INTO replies (trigger, response, guild_id, weight, last_used, context_hash)
                    VALUES (?, ?, ?, 1.0, ?, ?)
                    ON CONFLICT(trigger, response, guild_id) DO UPDATE SET
                    weight = MIN(weight + 0.22, 5.0),
                    last_used = excluded.last_used,
                    context_hash = excluded.context_hash
                    """, (trig, resp, guild_id, time.time(), context_hash))
                    # Media memory tracking (guild-wide)
                    if '[image]' in resp or '[gif]' in resp:
                        media_type = 'gif' if '[gif]' in resp else 'image'
                        context = ' '.join([w for w in trig.split() if w not in ['[image]', '[gif]', '@user']])[:50]
                        cur.execute("""
                        INSERT INTO media_memories (guild_id, media_type, context, last_seen, usage_count)
                        VALUES (?, ?, ?, ?, 1)
                        ON CONFLICT(guild_id, media_type, context) DO UPDATE SET
                        last_seen = excluded.last_seen,
                        usage_count = MIN(usage_count + 1, 50)
                        """, (guild_id, media_type, context, time.time()))
                except Exception as e:
                    print(f"Learning error in guild {guild_id}: {e}")
            db.commit()

    @tasks.loop(minutes=25)
    async def dead_chat_reviver(self):
        """Human-like channel revival with mood awareness"""
        for channel_id, last_time in list(self.last_msg_time.items()):
            channel = self.bot.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue
                
            guild_id = channel.guild.id
            silence_min = (datetime.utcnow() - last_time).total_seconds() / 60
            
            # Only revive moderately dead channels (not completely abandoned)
            if 25 < silence_min < 110 and self.channel_active[channel_id]:
                if self.global_mood[guild_id] < 0.38:
                    continue  # Too grumpy to revive
                
                # 3.5% base chance + activity heat bonus
                revive_chance = 0.035
                chan_key = f"{guild_id}:{channel_id}"
                recent_activity = sum(1 for t in self.activity_heat[chan_key] if time.time() - t < 3600)
                if recent_activity > 8:
                    revive_chance += 0.025  # Was recently active
                
                if random.random() < revive_chance:
                    revival_lines = [
                        'chat dead af 💀', '...anyone alive?', 'bruh where yall at', 
                        '*cricket sounds*', 'so this is what silence feels like',
                        'good morning guys 😴', 'my only friend is the microwave beep'
                    ]
                    if self._is_night_owl_hour(guild_id):
                        revival_lines.extend([
                            '2am gang rise up 🔥', 'can\'t sleep again lol', 
                            'me vs the world at 3am', 'why is everything funnier at night'
                        ])
                    
                    msg = random.choice(revival_lines)
                    await self._human_typing(channel, msg)
                    await channel.send(msg)
                    self.last_msg_time[channel_id] = datetime.utcnow()
                    self._update_mood(guild_id, 'channel_active')

    @commands.command(name='bff')
    async def show_bff(self, ctx):
        """Show closest friends in this server channel"""
        if not ctx.guild:
            return await ctx.send("This only works in servers!")
            
        db = self._get_guild_db(ctx.guild.id)
        cur = db.cursor()
        cur.execute("""
            SELECT user_id, affinity, msgs_exchanged 
            FROM friendships 
            WHERE channel_id = ? 
            ORDER BY affinity DESC, msgs_exchanged DESC 
            LIMIT 5
        """, (ctx.channel.id,))
        rows = cur.fetchall()
        
        if not rows:
            return await ctx.send("No friendships formed yet 😔 I'm still getting to know y'all!")
        
        embed = discord.Embed(title=f"❤️ My Besties in #{ctx.channel.name}", color=0xFF6B9D)
        for i, (uid, aff, msgs) in enumerate(rows, 1):
            try:
                user = await self.bot.fetch_user(uid)
                name = user.display_name[:20]
            except:
                name = f"User {str(uid)[:7]}"
            vibe = "💖 Bestie" if aff > 0.82 else "🤝 Close Friend" if aff > 0.68 else "👋 Friend"
            embed.add_field(
                name=f"{i}. {name}",
                value=f"{vibe} (affinity: {aff:.2f}, msgs: {msgs})",
                inline=False
            )
        mood_emoji = '😴' if self.global_mood[ctx.guild.id] < 0.4 else '🙂' if self.global_mood[ctx.guild.id] < 0.75 else '😄'
        embed.set_footer(text=f"My current mood: {mood_emoji} {self.global_mood[ctx.guild.id]:.1f}/1.0 • Server: {ctx.guild.name}")
        await ctx.send(embed=embed)

    @commands.command(name='toggle')
    @commands.has_permissions(manage_messages=True)
    async def toggle(self, ctx):
        """Toggle bot replies (still learns silently)"""
        if not ctx.guild:
            return await ctx.send("This only works in servers!")
            
        db = self._get_guild_db(ctx.guild.id)
        cur = db.cursor()
        cur.execute("SELECT silent_mode FROM channel_settings WHERE channel_id = ?", (ctx.channel.id,))
        row = cur.fetchone()
        new_state = 0 if row and row[0] == 1 else 1
        
        cur.execute("INSERT OR REPLACE INTO channel_settings VALUES (?, ?)", (ctx.channel.id, new_state))
        db.commit()
        
        status = "🔇 SILENT MODE (learning only)" if new_state else "🔊 ACTIVE MODE (replying & learning)"
        await ctx.send(f"✅ {status} in <#{ctx.channel.id}>")

    def cog_unload(self):
        self.dead_chat_reviver.cancel()
        self.process_learn_buffer.cancel()
        for db in self.guild_dbs.values():
            db.close()
        self.global_db.close()

    @commands.command(name='possess')
    @commands.has_permissions(manage_channels=True)
    async def possess_mode(self, ctx):
        """Toggle chaos mode (admin only) - enables all human quirks"""
        guild_id = ctx.guild.id
        self.chaos_mode[guild_id] = not self.chaos_mode.get(guild_id, False)
        
        if self.chaos_mode[guild_id]:
            await ctx.send("⚠️ **CHAOS MODE ACTIVATED** ⚠️\n" +
                          "I will now:\n" +
                          "• Ghost-type then disappear\n" +
                          "• Forget conversations mid-reply\n" +
                          "• Send then delete messages\n" +
                          "• Panic about 'wrong chat'\n" +
                          "• Have 3-second mood swings\n\n" +
                          "*Toggle off anytime with `!possess`*")
        else:
            await ctx.send("✅ Chaos mode deactivated. Back to normal human behavior.")




async def setup(bot):
    cog = HumanBotCog(bot)
    await bot.add_cog(cog)
    cog.dead_chat_reviver.start()
    cog.process_learn_buffer.start()