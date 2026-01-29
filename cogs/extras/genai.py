# membots/human_bot.py
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
    """Mimics real humans: reacts, replies, friendships, moods, ghost edits"""

    def __init__(self, bot):
        self.bot = bot
        self.db = sqlite3.connect("human_brain.db", check_same_thread=False)
        self._init_db()
        self.cooldowns = defaultdict(float)
        self.last_msg_time = defaultdict(lambda: datetime.utcnow())
        self.channel_active = defaultdict(lambda: True)
        self.global_mood = 0.7
        self.recent_replies = defaultdict(lambda: deque(maxlen=3))  # Prevent repetition
        
        # Human patterns (Indian Discord tested)
        self.patterns = [
            (r'kya hua|kya scene', ['timepass kar raha hu yaar 😂', 'ghanta pata hai but chill hai 🔥']),
            (r'accha|nice|mast', ['haan yaar bilkul 👍', 'vibe check passed ✅']),
            (r'lol|lmao|ded|dead', ['dead 💀', 'fr fr i\'m crying 😭', 'stop']),
            (r'why.*like this', ['jugaad hai bhai 😂', 'life hai yaar 🤷']),
            (r'thanks|thx|shukriya', ['koi baat nhi 🙏', 'bas chai pilaa de 😂']),
        ]
        
        # Reaction mappings (human-like emoji responses)
        self.reaction_map = [
            (r'lol|lmao|ded|dead|funny|😂', ['😂', '💀', '😭']),
            (r'yes|haa|yup|yeah|correct', ['✅', '👍', '💯']),
            (r'no|nahi|nop|ghanta', ['❌', '👎', '🚫']),
            (r'love|<3|heart|ily', ['❤️', '🥰', '😍']),
            (r'angry|gussa|mad|bsdk', ['😤', '😡', '🤬']),
            (r'sleep|tired|neend|zzz', ['😴', '💤', '🥱']),
            (r'fire|lit|sick|wow', ['🔥', '✨', '🤩']),
            (r'cringe|ew|yuck', ['🤢', '🤮', '😒']),
        ]
        
        self.base_reply_chance = 0.18
        self.mention_reply = 0.85
        self.night_hours = (23, 4)

    def _init_db(self):
        cur = self.db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS replies (
                trigger TEXT NOT NULL,
                response TEXT NOT NULL,
                channel_id INTEGER,
                weight REAL DEFAULT 1.0,
                last_used REAL DEFAULT 0,
                PRIMARY KEY (trigger, response, channel_id)
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
        self.db.commit()

    def _normalize(self, text):
        text = text.lower()
        text = re.sub(r'<@!?&?\d+>', '@user', text)
        text = re.sub(r'https?://\S+', '[link]', text)
        return ' '.join(text.split())[:150]

    async def _learn(self, prev, curr):
        if curr.author.bot or random.random() > 0.25 or len(curr.content) < 5:
            return
            
        trig = self._normalize(prev.content)
        resp = self._normalize(curr.content)
        if len(trig) < 4 or len(resp) < 3:
            return
            
        cur = self.db.cursor()
        try:
            cur.execute("""
                INSERT INTO replies (trigger, response, channel_id, weight, last_used)
                VALUES (?, ?, ?, 1.0, ?)
                ON CONFLICT DO UPDATE SET
                    weight = MIN(weight + 0.25, 5.0),
                    last_used = ?
            """, (trig, resp, curr.channel.id, time.time(), time.time()))
            self.db.commit()
        except: pass
        
        cur.execute("""
            INSERT INTO friendships (user_id, channel_id, affinity, last_interaction, msgs_exchanged)
            VALUES (?, ?, 0.5, ?, 1)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET
                affinity = MIN(affinity + 0.08, 1.0),
                last_interaction = ?,
                msgs_exchanged = msgs_exchanged + 1
        """, (curr.author.id, curr.channel.id, time.time(), time.time()))
        self.db.commit()

    def _get_affinity(self, user_id, channel_id):
        cur = self.db.cursor()
        cur.execute("""
            SELECT affinity, last_interaction FROM friendships
            WHERE user_id = ? AND channel_id = ?
        """, (user_id, channel_id))
        row = cur.fetchone()
        
        if not row:
            return 0.25
            
        affinity, last_int = row
        hours_since = (time.time() - last_int) / 3600
        decayed = max(0.3, affinity * (0.98 ** hours_since))
        return decayed

    def _update_mood(self, event='neutral'):
        now = time.time()
        if not hasattr(self, '_last_mood_update'):
            self._last_mood_update = now
            
        minutes_passed = (now - self._last_mood_update) / 60
        self.global_mood = max(0.2, self.global_mood - (0.03 * minutes_passed))
        
        boosts = {
            'lol': 0.12,
            'ded': 0.15,
            'thanks': 0.08,
            'ignored': -0.07,
            'long_conv': 0.10,
            'react_only': 0.05  # Reacting without replying = low effort = mood boost
        }
        self.global_mood = max(0.2, min(1.0, self.global_mood + boosts.get(event, 0)))
        self._last_mood_update = now

    async def _try_react(self, msg, affinity):
        """Human-like reaction behavior (sometimes just reacts, no reply)"""
        norm = self._normalize(msg.content)
        
        # Pick matching reaction
        for pattern, emojis in self.reaction_map:
            if re.search(pattern, norm):
                # Reaction chance scales with affinity + mood
                react_chance = 0.35 * affinity * self.global_mood
                
                # Higher chance at night (people react more when bored)
                if self._is_night_owl_hour():
                    react_chance *= 1.3
                
                if random.random() < react_chance:
                    try:
                        await msg.add_reaction(random.choice(emojis))
                        # 60% chance to STOP here (just reacted, no text reply)
                        if random.random() < 0.6:
                            self._update_mood('react_only')
                            return True  # Signal: skip text reply
                    except discord.HTTPException:
                        pass  # Ignore if can't react (permissions etc)
                break
        
        return False  # Didn't react OR reacted but will still reply

    def _is_question(self, text):
        """Detect questions naturally (not just '?')"""
        text_l = text.lower()
        question_words = ['kya', 'kyu', 'kaise', 'kahan', 'why', 'how', 'what', 'when', 'where', 'who']
        return (
            '?' in text or
            any(text_l.startswith(qw) for qw in question_words) or
            any(f' {qw} ' in text_l for qw in question_words[:4])  # Hindi Q-words anywhere
        )

    def _get_reply(self, text, channel_id, author, affinity):
        norm = self._normalize(text)
        self._update_mood()
        
        # Avoid repeating recent replies
        recent_set = set(self.recent_replies[channel_id])
        
        # Pattern match
        for pattern, replies in self.patterns:
            if re.search(pattern, norm) and random.random() < (0.75 * affinity * self.global_mood):
                candidates = [r for r in replies if r not in recent_set]
                if not candidates:  # All recent? Pick anyway but flag
                    candidates = replies
                reply = random.choice(candidates)
                self.recent_replies[channel_id].append(reply)
                
                if self._is_question(text):
                    reply = f"yaar {reply}" if affinity > 0.6 else reply
                elif 'lol' in norm or 'dead' in norm:
                    reply = f"bhai {reply}" if affinity > 0.7 else reply
                return reply
        
        # Learned replies
        words = norm.split()[:3]
        if words and random.random() < (0.6 * affinity * self.global_mood):
            cur = self.db.cursor()
            placeholders = ' OR '.join(['trigger LIKE ?'] * len(words))
            cur.execute(f"""
                SELECT response, weight FROM replies
                WHERE channel_id = ? AND ({placeholders})
                ORDER BY weight DESC LIMIT 15
            """, (channel_id, *[f'%{w}%' for w in words]))
            
            rows = cur.fetchall()
            if rows:
                # Filter out recent replies
                filtered = [(r, w) for r, w in rows if r not in recent_set]
                if not filtered and len(rows) > 3:
                    filtered = rows[:5]  # Fallback to top 5 if all recent
                
                if filtered:
                    total = sum(w for _, w in filtered)
                    pick = random.uniform(0, total)
                    curr = 0
                    for resp, w in filtered:
                        curr += w
                        if curr >= pick:
                            self.recent_replies[channel_id].append(resp)
                            if random.random() < 0.3:
                                resp = self._add_human_flaws(resp, affinity)
                            return resp
        
        # Fallbacks (mood-aware)
        if self.global_mood < 0.4:
            fallbacks = ['k', '...', 'mood off', 'later']
        else:
            fallbacks = ['lol', '💀', 'fr', 'no way', 'stop', 'facts', 'same']
        
        reply = random.choice(fallbacks)
        self.recent_replies[channel_id].append(reply)
        return reply if random.random() < 0.6 else None

    def _add_human_flaws(self, text, affinity):
        words = text.split()
        
        if random.random() < (0.15 * affinity) and len(words) > 2:
            typo_map = {'the': 'teh', 'and': 'abd', 'you': 'u', 'are': 'r'}
            for i, word in enumerate(words[:4]):
                if word.lower() in typo_map and random.random() < 0.6:
                    words[i] = typo_map[word.lower()]
                    break
        
        if random.random() < (0.3 * affinity):
            if any(x in text.lower() for x in ['lol', 'dead', 'funny']):
                text = text + ' 💀'
            elif any(x in text.lower() for x in ['nice', 'good', 'cool']):
                text = text + ' 👍'
        
        return ' '.join(words)

    def _is_night_owl_hour(self):
        ist_hour = (datetime.utcnow().hour + 5) % 24
        return ist_hour >= self.night_hours[0] or ist_hour <= self.night_hours[1]

    async def _human_typing(self, channel, text):
        wpm = random.randint(45, 65)
        chars = len(text)
        seconds = max(0.4, min(3.5, chars / (wpm * 5 / 60)))
        async with channel.typing():
            await asyncio.sleep(seconds + random.uniform(-0.3, 0.5))

    async def _smart_send(self, msg, reply, affinity):
        """Human sending with context-aware reply vs. send"""
        # 4% chance: type → disappear
        if random.random() < 0.04:
            async with msg.channel.typing():
                await asyncio.sleep(random.uniform(1.5, 3.0))
            self._update_mood('ignored')
            return
        
        await self._human_typing(msg.channel, reply)
        
        # GHOST EDIT: 7% chance for close friends
        if affinity > 0.7 and random.random() < 0.07 and len(reply) > 8:
            words = reply.split()
            if len(words) > 2:
                typo_idx = random.randint(0, min(2, len(words)-1))
                if len(words[typo_idx]) > 3:
                    word = words[typo_idx]
                    words[typo_idx] = word[:-2] + word[-1] + word[-2]  # "cool" → "col"
                    typo_msg = ' '.join(words)
                    
                    sent = await msg.channel.send(typo_msg)
                    await asyncio.sleep(random.uniform(1.4, 2.2))
                    await sent.edit(content=reply)
                    return
        
        # CONTEXT-AWARE: Reply to questions, just send for casual
        if self._is_question(msg.content):
            # Use Discord's reply feature (creates visual thread)
            await msg.reply(reply[:1900], mention_author=False)
        else:
            # Casual chat = just send to channel (no ping)
            await msg.channel.send(reply[:1900])

    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot or not msg.guild or msg.content.startswith(tuple('!?.#$%&*')):
            return
        
        # Learn + update friendship
        if not msg.author.bot:
            async for prev in msg.channel.history(limit=2):
                if prev.id != msg.id and not prev.author.bot:
                    await self._learn(prev, msg)
                    break
        
        self.last_msg_time[msg.channel.id] = datetime.utcnow()
        self.channel_active[msg.channel.id] = True
        
        # Check silent mode
        cur = self.db.cursor()
        cur.execute("SELECT silent_mode FROM channel_settings WHERE channel_id = ?", (msg.channel.id,))
        row = cur.fetchone()
        if row and row[0] == 1:
            return
        
        affinity = self._get_affinity(msg.author.id, msg.channel.id)
        reply_chance = self.base_reply_chance * affinity * self.global_mood
        
        if self._is_night_owl_hour():
            reply_chance *= 1.4
        
        # HUMAN BEHAVIOR: Sometimes just react (no reply)
        reacted_and_done = await self._try_react(msg, affinity)
        if reacted_and_done:
            return  # Skipped text reply — just reacted like a human would
        
        # Decide to reply
        should_reply = (
            (self.bot.user in msg.mentions and random.random() < (self.mention_reply * affinity)) or
            (random.random() < reply_chance and len(msg.content.split()) > 2)
        )
        
        cooldown = 12.0 if affinity < 0.4 else 7.0
        if time.time() - self.cooldowns[msg.author.id] < cooldown:
            should_reply = False
        
        if should_reply:
            self.cooldowns[msg.author.id] = time.time()
            reply = self._get_reply(msg.content, msg.channel.id, msg.author, affinity)
            if reply:
                await self._smart_send(msg, reply, affinity)
                if any(x in reply.lower() for x in ['lol', 'dead', '💀']):
                    self._update_mood('lol' if 'lol' in reply else 'ded')
        else:
            if affinity > 0.6 and random.random() < 0.3:
                self._update_mood('ignored')

    @tasks.loop(minutes=32)
    async def dead_chat_reviver(self):
        for channel_id, last_time in list(self.last_msg_time.items()):
            silence = (datetime.utcnow() - last_time).total_seconds() / 60
            
            if 30 < silence < 120 and self.channel_active[channel_id]:
                channel = self.bot.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                if self.global_mood < 0.35:
                    continue
                    
                if random.random() < 0.04:
                    revival_lines = [
                        'chat dead af 💀',
                        '...anyone alive?',
                        'bruh where yall at',
                        '*cricket sounds*',
                        'so this is what silence feels like',
                        'good morning guys 😴',
                    ]
                    if self._is_night_owl_hour():
                        revival_lines.extend(['2am gang rise up 🔥', 'can\'t sleep again lol'])
                    
                    msg = random.choice(revival_lines)
                    await self._human_typing(channel, msg)
                    await channel.send(msg)
                    self.last_msg_time[channel_id] = datetime.utcnow()

    @dead_chat_reviver.before_loop
    async def before_reviver(self):
        await self.bot.wait_until_ready()

    @commands.command(name='bff')
    async def show_bff(self, ctx):
        """Show who the bot is closest to in this channel"""
        cur = self.db.cursor()
        cur.execute("""
            SELECT user_id, affinity, msgs_exchanged 
            FROM friendships 
            WHERE channel_id = ? 
            ORDER BY affinity DESC, msgs_exchanged DESC 
            LIMIT 5
        """, (ctx.channel.id,))
        rows = cur.fetchall()
        
        if not rows:
            await ctx.send("No friendships formed yet 😔 I'm still getting to know y'all!")
            return
        
        embed = discord.Embed(title="❤️ My Besties Here", color=0xFF6B9D)
        for i, (uid, aff, msgs) in enumerate(rows, 1):
            try:
                user = await self.bot.fetch_user(uid)
                name = user.display_name[:18]
            except:
                name = f"User {str(uid)[:6]}"
            vibe = "💖 Bestie" if aff > 0.8 else "🤝 Friend" if aff > 0.6 else "👋 Acquaintance"
            embed.add_field(
                name=f"{i}. {name}",
                value=f"{vibe} (affinity: {aff:.2f}, msgs: {msgs})",
                inline=False
            )
        mood_emoji = '😴' if self.global_mood < 0.4 else '🙂' if self.global_mood < 0.7 else '😄'
        embed.set_footer(text=f"My current mood: {mood_emoji} {self.global_mood:.1f}/1.0")
        await ctx.send(embed=embed)

    @commands.command(name='toggle')
    @commands.has_permissions(manage_messages=True)
    async def toggle(self, ctx):
        """Toggle bot replies (still learns silently)"""
        cur = self.db.cursor()
        cur.execute("SELECT silent_mode FROM channel_settings WHERE channel_id = ?", (ctx.channel.id,))
        row = cur.fetchone()
        new_state = 0 if row and row[0] == 1 else 1
        
        cur.execute("INSERT OR REPLACE INTO channel_settings VALUES (?, ?)", (ctx.channel.id, new_state))
        self.db.commit()
        
        status = "🔇 SILENT (learning only)" if new_state else "🔊 ACTIVE (replying & learning)"
        await ctx.send(f"✅ {status}")

    @commands.command(name='brainstats')
    async def show_stats(self, ctx):
        """Show learning stats in a human way (no robotic numbers)"""
        cur = self.db.cursor()
        
        # Get channel-specific stats
        cur.execute("SELECT COUNT(*) FROM replies WHERE channel_id = ?", (ctx.channel.id,))
        patterns = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM friendships WHERE channel_id = ?", (ctx.channel.id,))
        friends = cur.fetchone()[0]
        
        # Mood context
        mood_emoji = '😴' if self.global_mood < 0.4 else '🙂' if self.global_mood < 0.7 else '😄'
        mood_desc = (
            "grumpy rn (spam me less 😤)" if self.global_mood < 0.4 else
            "chill vibes ✌️" if self.global_mood < 0.7 else
            "hyperactive mode 🔥"
        )
        
        # Night owl status
        night_status = "🌙 Night Owl Mode" if self._is_night_owl_hour() else "☀️ Day Mode"
        
        embed = discord.Embed(
            title="🧠 My Brain Stats",
            color=0x5865F2,
            description=(
                f"**I've learned:** {patterns} ways to reply here\n"
                f"**Friends made:** {friends} people\n"
                f"**Current vibe:** {mood_emoji} {mood_desc}\n"
                f"**Server time:** {night_status}\n\n"
                "*I just learn from what you all say!* 🤖✨"
            )
        )
        embed.set_footer(text="P.S. I ignore newbies until they prove they're not bots 😉")
        await ctx.send(embed=embed)

    def cog_unload(self):
        self.dead_chat_reviver.cancel()
        self.db.close()

async def setup(bot):
    cog = HumanBotCog(bot)
    await bot.add_cog(cog)
    cog.dead_chat_reviver.start()