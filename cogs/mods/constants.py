"""Constants and configuration defaults for the Staff Analytics cog."""

import re
from discord import Colour

# ─── Time windows (seconds unless noted) ─────────────────────────────────────
INTERACTION_WINDOW        = 60
UNIQUE_USER_WINDOW        = 21600   # 6 h de-dupe window for unique members
CONVERSATION_TIMEOUT      = 600     # 10 min gap = new session
MODERATION_SESSION_WINDOW = 900     # 15 min between mod actions = same session
REPORT_COOLDOWN           = 300     # 5 min cooldown per channel
MIN_VC_SESSION            = 1.0     # MINUTES — ignore drive-by VC joins
INACTIVITY_DAYS           = 10
ROLE_PING_MIN_MEMBERS     = 3
RESPONSE_TIME_WINDOW      = 300     # 5 min — reply within this = "fast response"
NEW_MEMBER_THRESHOLD_DAYS = 12      # member age <= this = new-member help bonus
FIRST_RESPONSE_WINDOW     = 180     # 3 min — if NO other staff messaged before, it's a first response
QUALITY_MIN_WORDS         = 8       # minimum words to earn a quality event credit

# ─── Ping-to-action latency ───────────────────────────────────────────────────
# Seconds after a @Moderator/@Staff ping to credit a "ping response"
PING_RESPONSE_WINDOW = 300   # 5 min

# ─── VC people bonus ──────────────────────────────────────────────────────────
# Staff gets bonus points per additional person in VC with them
VC_PEOPLE_BONUS_PER_PERSON = 0.5     # score multiplier per co-occupant above 1
# Minimum minutes in VC before people-bonus is credited
VC_MIN_DURATION_FOR_PEOPLE_BONUS = 5  # 5 minutes minimum — prevents join-leave gaming

# ─── Newbie chat bonus ────────────────────────────────────────────────────────
NEWBIE_CHAT_SCORE_BONUS = 3.0        # extra points per unique new member talked TO

# ─── Achievement decay ────────────────────────────────────────────────────────
# Achievements older than this many days lose their "shine" (shown faded in UI)
ACHIEVEMENT_DECAY_DAYS = 60          # achievements earned > 60 days ago are "faded"

# ─── Bot IDs ─────────────────────────────────────────────────────────────────
import os as _os
DISBOARD_BOT_ID = 302050872383242240   # Official Disboard bump bot
STAFF_DB_PATH   = _os.getenv("STAFF_DB_PATH", "data/staff_analytics.db")

# ─── Keyword sets ─────────────────────────────────────────────────────────────
HELP_KEYWORDS_STRONG: frozenset = frozenset({
    "here's how", "try this", "have you tried", "step by step",
    "the fix is", "the solution is", "to fix this", "to resolve this",
    "to solve this", "you could try", "maybe try", "i suggest",
    "i'd suggest", "i'd recommend", "i recommend", "tutorial",
    "walkthrough", "here is a guide", "here's a guide",
    "check this out", "take a look at this",
})

HELP_KEYWORDS_WEAK: frozenset = frozenset({
    "solution", "assist", "support", "guide",
    "you could", "another option", "one way to", "check out", "take a look",
})

def _build_kw_pattern(keywords: frozenset) -> re.Pattern:
    phrases = sorted(keywords, key=len, reverse=True)
    alts = "|".join(re.escape(p) for p in phrases)
    return re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)", re.IGNORECASE)

HELP_STRONG_RE: re.Pattern = _build_kw_pattern(HELP_KEYWORDS_STRONG)
HELP_WEAK_RE:   re.Pattern = _build_kw_pattern(HELP_KEYWORDS_WEAK)
HELP_KEYWORDS: frozenset = HELP_KEYWORDS_STRONG | HELP_KEYWORDS_WEAK

REPORT_KEYWORDS: frozenset = frozenset({
    "report", "reports", "reporting", "reported", "report this", "report user",
    "report message", "file a report", "submit a report", "issue", "proof",
    "suspicious", "suspect", "suspected", "sus", "sketchy", "dodgy", "shady",
    "fishy", "questionable", "concerning behavior", "flag", "flags", "flagged",
    "flagging", "red flag", "raise a flag", "violating", "violates", "violation",
    "broke the rules", "breaks rules", "breaking rules", "against the rules",
    "rule break", "rule breaker", "rules broken", "against policy", "policy violation",
    "terms violation", "tos violation", "violates tos", "breaks tos", "against tos",
    "terms of service", "community guidelines", "guidelines violation",
    "against guidelines", "harassment", "harassing", "harass", "harassed",
    "bullying", "bully", "bullied", "targeted harassment", "targeted",
    "being targeted", "spam", "spamming", "spammer", "bot spam", "ad spam",
    "promotion spam", "excessive spam", "mass ping", "mass mention",
    "toxic", "toxicity", "hate speech", "hateful", "discrimination",
    "discriminatory", "racist", "racism", "sexist", "sexism", "homophobic",
    "homophobia", "transphobic", "transphobia", "xenophobic", "xenophobia",
    "threat", "threats", "threatening", "threatened", "violent", "violence",
    "harm", "hurting", "endanger", "endangering", "doxx", "doxxing", "doxed",
    "personal info", "private info", "private information", "personal information",
    "nsfw", "explicit", "inappropriate", "unsuitable", "nsfl", "gore",
    "graphic content", "adult content", "18+ content", "impersonating",
    "impersonation", "impersonate", "pretending to be", "fake account",
    "copying", "pretending", "scam", "scamming", "scammer", "fraud",
    "fraudulent", "phishing", "phish", "fake giveaway", "fake give away",
    "fake promo", "fake promotion", "malware", "virus", "malicious",
    "malicious link", "dangerous link", "unsafe link", "suspicious link",
    "phishing link", "raid", "raiding", "raider", "raiders", "raiding server",
    "raiding channel", "brigading", "brigade", "ban evasion", "evading ban",
    "evading", "alt account", "sock puppet", "sockpuppet", "alternate account",
    "trolling", "troll", "trolling behavior", "griefing", "grief", "sealioning",
    "concern trolling", "bad faith", "bad actor", "channel hijack", "hijacking",
    "channel takeover", "spam pings", "mass @", "mass ping", "spam mentions",
    "user report", "member report", "report member", "report user",
    "ban appeal", "appeal ban", "emergency", "urgent", "urgent report",
    "immediate attention", "needs mod", "moderator needed", "admin needed",
    "circumventing", "circumvention", "bypassing", "bypass", "avoiding mods",
    "self harm", "self harm threat", "suicide threat", "suicidal",
})

REPORT_RE: re.Pattern = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(REPORT_KEYWORDS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)

MODERATION_COMMANDS: frozenset = frozenset({
    "ban", "kick", "timeout", "warn", "mute", "unmute",
    "lock", "unlock", "slowmode", "purge", "clear",
    "softban", "unban", "strike",
})

MODERATION_COMMAND_RE: re.Pattern = re.compile(
    r"(?<!\w)(?:ban(?:ned)?|kick(?:ed)?|warn(?:ed|ing)?|muted?|timeout(?:ed)?|"
    r"unmuted?|unbanned?|softbanned?|strike|purge[d]?|clear(?:ed)?|"
    r"lock(?:ed)?|unlock(?:ed)?|slowmode)(?!\w)",
    re.IGNORECASE,
)

# Wick-style result embed patterns
# Wick sends embeds like: "Warn result: ... Reason: ... Moderator: @X ... Warned: @Y [id]"
WICK_RESULT_RE: re.Pattern = re.compile(
    r"(?:warn|ban|kick|mute|timeout|strike|softban)\s+result",
    re.IGNORECASE,
)

QUESTION_RE: re.Pattern = re.compile(
    r"(?:\w.*?\?|^.{10,}\?)",
    re.DOTALL,
)

QUESTION_KEYWORDS: frozenset = frozenset({
    "how do", "how can", "how to", "can someone", "anyone know",
    "does anyone", "is there a way", "help me", "help with",
    "not working", "broken", "error", "stuck", "confused",
})

# ─── Staff/mod role ping detection ───────────────────────────────────────────
# When a message mentions one of these role names, track ping-to-action latency
STAFF_ROLE_PING_KEYWORDS: frozenset = frozenset({
    "mod", "mods", "moderator", "moderators", "staff", "admin", "admins",
    "helper", "helpers", "support", "team",
})

# ─── Engagement keyword sets ──────────────────────────────────────────────────
GREETING_KEYWORDS: frozenset = frozenset({
    "welcome", "welcomes", "welcoming", "welcome aboard", "welcome to the server",
    "welcome to the community", "glad you're here", "glad you joined",
    "happy to have you", "happy you're here", "happy you joined",
    "so glad you're here", "so glad you joined", "thrilled to have you",
    "excited to have you", "excited you're here", "nice to meet",
    "nice to meet you", "pleasure to meet", "lovely to meet", "great to meet",
    "good to meet", "good to see you", "great to see you", "awesome to see you",
    "wonderful to see you", "always good to see", "what are you into",
    "what are your interests", "what do you enjoy", "what brings you here",
    "what brought you here", "how did you find us", "where are you from",
    "where in the world", "what timezone", "what's your timezone",
    "how long have you", "been here before", "first time here",
    "enjoy your stay", "hope you enjoy", "hope you like it", "hope you have fun",
    "hope you love it", "make yourself at home", "feel at home", "settle in",
    "get comfortable", "great to have you", "awesome to have you",
    "amazing to have you", "fantastic to have you", "wonderful to have you",
    "happy you found us", "glad you found us", "glad you discovered",
    "feel free to ask", "don't hesitate to ask", "let us know if",
    "reach out if", "any questions just", "if you need anything",
    "if you need help", "if you have questions", "introduce yourself",
    "tell us about yourself", "tell us a bit", "share a bit",
    "tell us about you", "introduce when you're ready",
    "feel free to introduce", "take a moment to introduce",
    "we're happy you're here", "we're glad you're here",
    "we're excited you're here", "you're part of the community",
    "you're part of the family", "glad to have you with us",
    "happy to have you with us", "what games do you play",
    "what do you do for fun", "what are your hobbies", "any hobbies",
    "interests outside", "what brings you joy", "woohoo", "w00t", "yay",
    "awesome", "amazing", "fantastic",
})

CONVERSATION_STARTER_KEYWORDS: frozenset = frozenset({
    "what do you think", "what do you all think", "what does everyone think",
    "what are your thoughts", "any thoughts on", "opinions on", "your take on",
    "what's your take", "how do you feel about", "what's your opinion",
    "anyone here", "anyone around", "anybody here", "anybody around",
    "anyone interested", "anybody interested", "anyone want to",
    "anybody want to", "anyone else", "anybody else", "anyone up for",
    "anybody up for", "hot take", "unpopular opinion", "controversial opinion",
    "am I the only one", "does anyone else", "who else thinks", "who agrees",
    "am I wrong for thinking", "poll:", "quick poll", "strawpoll", "vote:",
    "voting:", "question of the day", "qotd", "discussion question", "debate:",
    "what's everyone", "what is everyone", "what are everyone", "what are you all",
    "what's everyone up to", "what is everyone up to", "what are you all up to",
    "what's everyone doing", "what is everyone doing", "what are you all doing",
    "what's everyone playing", "what is everyone playing", "what are you all playing",
    "what's everyone watching", "what is everyone watching", "what are you all watching",
    "who here", "who's here", "who else", "who all", "who's playing",
    "who's watching", "who's interested", "who's down for", "who wants to",
    "would you rather", "wyrd", "wyr", "would you prefer", "which would you choose",
    "pick one:", "choose one:", "what's your", "what is your", "what are your",
    "what's your favorite", "what is your favorite", "what are your favorites",
    "what's your go-to", "what is your go-to", "what's your opinion on",
    "what is your opinion on", "rate this", "rate it", "review this",
    "what would you rate", "out of ten", "/10", "scale of 1 to 10", "on a scale",
    "game time", "let's play a game", "time for a game", "mini game", "word game",
    "guess the", "never have I ever", "two truths one lie", "this or that",
    "fun fact", "fun facts", "did you know", "interesting fact", "today I learned",
    "til", "random fact", "here's a fact", "let's talk about", "lets talk about",
    "let's discuss", "lets discuss", "discussion:", "topic:", "subject:",
    "speaking of", "on the topic of", "regarding", "what if", "imagine if",
    "suppose", "hypothetically", "if you could", "if you had to", "if you were",
    "recommend me", "suggestions for", "any recommendations", "what should I",
    "what do you recommend", "looking for recommendations", "weekend plans",
    "plans for the weekend", "any plans for", "what are you doing this weekend",
    "just watched", "just finished", "just started", "currently watching",
    "currently playing", "currently reading", "binging", "binge watching",
    "anyone else watching", "anyone else playing", "anyone else read",
    "random question", "icebreaker", "get to know you", "getting to know",
    "would you rather", "favorite thing", "what's something",
    "excited for", "looking forward to", "hyped for", "countdown to",
    "who's ready for", "need advice", "looking for advice",
    "seeking recommendations", "help me decide", "what would you do",
})

GREETING_RE: re.Pattern             = _build_kw_pattern(GREETING_KEYWORDS)
CONVERSATION_STARTER_RE: re.Pattern = _build_kw_pattern(CONVERSATION_STARTER_KEYWORDS)

# ─── Moderation severity weights ─────────────────────────────────────────────
MODERATION_SEVERITY: dict = {
    "ban":       3.0, "softban":   2.5, "kick":      2.0,
    "timeout":   1.5, "mute":      1.5, "warn":      1.0,
    "strike":    1.0, "purge":     1.5, "clear":     1.5,
    "lock":      0.5, "unlock":    0.3, "slowmode":  0.4,
    "unmute":    0.4, "unban":     0.5,
}

# ─── Quality signal multipliers ───────────────────────────────────────────────
QUALITY_SIGNALS: dict = {
    "code_block":    2.5, "inline_code":   0.6,
    "link":          1.8, "numbered_list": 1.2,
    "bullet_list":   0.8, "long_message":  1.0,
    "image":         1.5, "quote":         0.5,
}

QUALITY_SCORE_THRESHOLD = 1.5

# ─── Hours / coverage ────────────────────────────────────────────────────────
PEAK_HOURS:     range = range(14, 23)
OFF_PEAK_HOURS: list  = list(range(0, 8)) + list(range(22, 24))

MAX_HELP_EVENTS_PER_USER_PER_DAY = 3
MAX_REACTIONS_PER_DAY            = 50
MAX_INTERACTIONS_PER_MESSAGE     = 2   # cap unique interaction credits per single message
MAX_REPORTS_PER_HOUR             = 10
MAX_GREETINGS_PER_USER_PER_DAY   = 1
MAX_STARTERS_PER_DAY             = 10
MAX_REACTIONS_RECEIVED_PER_MSG   = 10
DEAD_CHAT_THRESHOLD              = 1800   # 30 min
REVIVAL_RESPONSE_WINDOW          = 600    # 10 min

# ─── Score weights (defaults; overridable per guild via DB) ───────────────────
SCORE_WEIGHTS: dict = {
    # ── Community ──
    "unique_members":        2.0,
    "help_events":           3.0,
    "proactive_help":        3.0,
    "new_member_helped":     5.0,   # helped a new member
    "newbie_chat":           3.0,   # talked WITH a new member (not necessarily helped)
    "reactions_given":       0.5,
    "threads_created":       1.5,
    "new_member_chat":       1.5,
    # ── Moderation ──
    "moderation_actions":    2.0,
    "moderation_severity":   2.0,
    "reports":               3.0,
    # ── Presence ──
    "vc_hours":              1.5,
    "vc_people_bonus":       0.5,   # per extra person in VC
    "conversation_sessions": 1.5,
    "server_bumps":          3.0,
    # ── Quality & Speed ──
    "fast_response":         1.5,
    "first_response":        3.0,
    "ping_response":         4.0,   # responded to a @Staff ping quickly
    "message_quality":       0.12,
    "quality_event":         1.5,
    # ── Consistency ──
    "daily_activity":        5.0,
    "hourly_spread":         3.0,
    "peak_hours_bonus":      2.5,
    "off_peak_coverage":     3.5,
    "streak_bonus":          7.0,
    # ── Engagement (community building) ──
    "dead_chat_revival":     6.0,
    "conversation_starter":  4.5,
    "new_member_greeting":   3.0,
    "reactions_received":    0.3,
    # ── Unique people talked with ──
    "unique_talked_with":    1.5,   # per unique non-staff person talked to (not just helped)
}

# ─── KPI Targets (per 30 days) — used for progress display ───────────────────
KPI_TARGETS: dict = {
    "moderation_actions":   20,
    "vc_hours":             10.0,
    "conversation_sessions":30,
    "unique_members":       40,
    "fast_responses":       25,
    "first_responses":      15,
    "new_members_helped":   10,
    "reactions_given":      50,
    "threads_created":      5,
    "active_days":          20,
    "engagement_score":     500,
    "dead_chat_revivals":    5,
    "conversation_starters": 10,
    "new_member_greetings":  15,
    "reactions_received":    30,
    "unique_talked_with":    50,
    "ping_responses":        10,
}

# ─── Rank tier thresholds ────────────────────────────────────────────────────
RANK_TIERS: dict = {
    "Diamond":  {"min": 2001, "emoji": "💎", "colour": Colour.from_str("#00CFFF"), "label": "Elite"},
    "Platinum": {"min": 1001, "emoji": "🌟", "colour": Colour.from_str("#E8E8FF"), "label": "Expert"},
    "Gold":     {"min":  501, "emoji": "🥇", "colour": Colour.gold(),              "label": "Proficient"},
    "Silver":   {"min":  201, "emoji": "🥈", "colour": Colour.light_grey(),        "label": "Developing"},
    "Bronze":   {"min":    0, "emoji": "🥉", "colour": Colour.from_str("#CD7F32"), "label": "Newcomer"},
}

# ─── Achievement definitions ──────────────────────────────────────────────────
# Each achievement has a decay_days field — if earned > decay_days ago it shows faded
ACHIEVEMENT_THRESHOLDS: dict = {
    # help_events removed (metric no longer produced) — replaced with real tracked metrics
    "greeter":          {"metric": "new_member_greetings",  "value": 5,    "emoji": "👋",  "desc": "5 new members greeted",       "decay_days": None},
    "helper":           {"metric": "new_member_greetings",  "value": 25,   "emoji": "🤝",  "desc": "25 new members greeted",      "decay_days": 60},
    "sensei":           {"metric": "unique_talked_with",    "value": 50,   "emoji": "🧑‍🏫", "desc": "Talked with 50 unique people", "decay_days": 90},
    "guardian":         {"metric": "moderation_actions",    "value": 25,   "emoji": "🛡️",  "desc": "25 mod actions",              "decay_days": 60},
    "enforcer":         {"metric": "moderation_actions",    "value": 100,  "emoji": "⚖️",  "desc": "100 mod actions",             "decay_days": 90},
    "voice_of_reason":  {"metric": "vc_hours",              "value": 10.0, "emoji": "🎙️",  "desc": "10 VC hours",                 "decay_days": 60},
    "broadcaster":      {"metric": "vc_hours",              "value": 50.0, "emoji": "📡",  "desc": "50 VC hours",                 "decay_days": 90},
    "always_on":        {"metric": "current_streak",        "value": 7,    "emoji": "🔥",  "desc": "7-day streak",                "decay_days": 14},
    "unstoppable":      {"metric": "current_streak",        "value": 30,   "emoji": "⚡",  "desc": "30-day streak",               "decay_days": 30},
    "onboarding_hero":  {"metric": "new_members_helped",    "value": 20,   "emoji": "🌱",  "desc": "20 new members helped",       "decay_days": 60},
    "speed_demon":      {"metric": "fast_responses",        "value": 25,   "emoji": "💨",  "desc": "25 fast responses",           "decay_days": 60},
    "first_responder":  {"metric": "first_responses",       "value": 20,   "emoji": "🚀",  "desc": "20 first-to-respond",         "decay_days": 60},
    "thread_master":    {"metric": "threads_created",       "value": 15,   "emoji": "🧵",  "desc": "15 threads created",          "decay_days": None},
    "reactor":          {"metric": "reactions_given",       "value": 100,  "emoji": "😊",  "desc": "100 reactions given",         "decay_days": None},
    "night_owl":        {"metric": "off_peak_messages",     "value": 50,   "emoji": "🦉",  "desc": "50 off-peak messages",        "decay_days": None},
    "conversationalist":{"metric": "conversation_starters", "value": 10,   "emoji": "💡",  "desc": "10 conversations started",    "decay_days": 60},
    "social_butterfly": {"metric": "new_member_greetings",  "value": 25,   "emoji": "🦋",  "desc": "25 new members greeted",      "decay_days": 60},
    "reviver":          {"metric": "dead_chat_revivals",    "value": 5,    "emoji": "🔋",  "desc": "5 dead chats revived",        "decay_days": 60},
    "crowd_pleaser":    {"metric": "reactions_received",    "value": 50,   "emoji": "🌟",  "desc": "50 reactions received",       "decay_days": 60},
    "people_person":    {"metric": "unique_talked_with",    "value": 100,  "emoji": "🫂",  "desc": "Talked with 100 unique people","decay_days": None},
    "rapid_responder":  {"metric": "ping_responses",        "value": 10,   "emoji": "📟",  "desc": "10 @staff pings answered",    "decay_days": 60},
    "vc_social":        {"metric": "vc_people_sessions",    "value": 20,   "emoji": "🎧",  "desc": "20 VC sessions with others",  "decay_days": None},
    "newbie_friend":    {"metric": "unique_newbies_chatted","value": 30,   "emoji": "🌈",  "desc": "Chatted with 30 new members", "decay_days": None},
}

# ─── Consistency windows ──────────────────────────────────────────────────────
CONSISTENCY_WINDOWS = {
    "morning":    ( 5, 12),
    "afternoon":  (12, 17),
    "evening":    (17, 22),
    "late_night": (22,  5),
}

# ─── Embed colours ────────────────────────────────────────────────────────────
EMBED_COLORS = {
    "primary": Colour.from_str("#5865F2"),
    "success": Colour.from_str("#57F287"),
    "warning": Colour.from_str("#FEE75C"),
    "error":   Colour.from_str("#ED4245"),
    "info":    Colour.from_str("#EB459E"),
    "neutral": Colour.from_str("#99AAB5"),
}

# ─── Database constants ───────────────────────────────────────────────────────
DB_BATCH_SIZE     = 100
DB_BATCH_TIMEOUT  = 5
DB_RETENTION_DAYS = 90
CACHE_TTL      = 300
CACHE_MAX_SIZE = 1000
MSG_QUALITY_WORD_THRESHOLD = QUALITY_MIN_WORDS

# ─── Display constants ────────────────────────────────────────────────────────
HEATMAP_CHARS  = ("░", "▒", "▓", "█")
PROGRESS_CHARS = ("░", "▒", "▓", "█")
MEDAL_EMOJIS   = ("🥇", "🥈", "🥉")
LEADERBOARD_LIMIT = 15

# ─── Rank-up notification guard ───────────────────────────────────────────────
# Minimum pts ABOVE the tier threshold before a rank-up DM fires.
# Prevents oscillation when a score sits right on the boundary.
RANKUP_HYSTERESIS = 12   # must be this many pts above tier minimum

# ─── Daily challenges ─────────────────────────────────────────────────────────
# Each challenge has a unique key, display label, metric (maps to StaffStats attr
# or special key), target count, and emoji.  Resets every UTC midnight.
DAILY_CHALLENGES: list = [
    {"key": "msgs_20",     "label": "Send 20 messages",          "metric": "total_messages",     "target": 20,  "emoji": "💬", "pts": 10},
    {"key": "mod_1",       "label": "Take 1 mod action",         "metric": "moderation_actions", "target": 1,   "emoji": "🛡️", "pts": 15},
    {"key": "vc_30",       "label": "30 min in VC",              "metric": "vc_hours",            "target": 0.5, "emoji": "🎙️", "pts": 12},
    {"key": "unique_5",    "label": "Chat with 5 unique users",  "metric": "unique_talked_with",  "target": 5,   "emoji": "🫂", "pts": 10},
    {"key": "react_5",     "label": "Give 5 reactions",          "metric": "reactions_given",     "target": 5,   "emoji": "😀", "pts": 5},
    {"key": "first_resp",  "label": "Be first to respond once",  "metric": "first_responses",     "target": 1,   "emoji": "🚀", "pts": 15},
    {"key": "greet_1",     "label": "Greet a new member",        "metric": "new_member_greetings","target": 1,   "emoji": "👋", "pts": 10},
    {"key": "quality_3",   "label": "Send 3 quality messages",   "metric": "quality_events",      "target": 3,   "emoji": "✨", "pts": 12},
    {"key": "streak_keep", "label": "Keep your streak alive",    "metric": "_active_today",       "target": 1,   "emoji": "🔥", "pts": 20},
    {"key": "ping_resp",   "label": "Answer a @staff ping",      "metric": "ping_responses",      "target": 1,   "emoji": "📟", "pts": 15},
]