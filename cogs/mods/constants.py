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

# ─── Keyword sets ─────────────────────────────────────────────────────────────
#
# BUG FIX: The original set used plain `kw in content` substring matching, which
# caused massive false positives at scale.  For example:
#   "you can"    → fires on "you can see the error in line 3" (any statement)
#   "here is"    → fires on "here is my problem:" (user complaint, not help)
#   "one way"    → fires on "there's no one way to do it" (negative statement)
#   "you need to"→ fires on "you need to provide more info" (clarification request)
#   "fix"        → fires on "prefix", "postfix", "suffix", "hotfix"
#
# SOLUTION: Split keywords into two tiers.
#   STRONG: phrases distinctive enough that a single match is sufficient evidence.
#   WEAK:   common words that only count when two or more appear in the same message,
#           or when the message is also a reply to a question.
#
# Both tiers use word-boundary compiled patterns (not substring).  All matching
# is done via HELP_STRONG_RE / HELP_WEAK_RE below instead of the raw sets.

# Strong signals — one is enough to flag a help intent
HELP_KEYWORDS_STRONG: frozenset = frozenset({
    "here's how",
    "try this",
    "have you tried",
    "step by step",
    "the fix is",
    "the solution is",
    "to fix this",
    "to resolve this",
    "to solve this",
    "you could try",
    "maybe try",
    "i suggest",
    "i'd suggest",
    "i'd recommend",
    "i recommend",
    "tutorial",
    "walkthrough",
    "here is a guide",
    "here's a guide",
    "check this out",
    "take a look at this",
})

# Weak signals — require at least 2 hits, OR 1 hit + replied-to question
HELP_KEYWORDS_WEAK: frozenset = frozenset({
    "solution",
    "assist",
    "support",
    "guide",
    "you could",
    "another option",
    "one way to",
    "check out",
    "take a look",
})

# Pre-compiled patterns for O(1)-ish matching (important at 24 k msgs/day).
# Using \b word boundaries so "fix" won't match "prefix" etc.
def _build_kw_pattern(keywords: frozenset) -> re.Pattern:
    # Sort longest first so alternation greedily matches the most specific phrase.
    phrases = sorted(keywords, key=len, reverse=True)
    alts = "|".join(re.escape(p) for p in phrases)
    return re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)", re.IGNORECASE)

HELP_STRONG_RE: re.Pattern = _build_kw_pattern(HELP_KEYWORDS_STRONG)
HELP_WEAK_RE:   re.Pattern = _build_kw_pattern(HELP_KEYWORDS_WEAK)

# Kept for back-compat but no longer used for matching.
HELP_KEYWORDS: frozenset = HELP_KEYWORDS_STRONG | HELP_KEYWORDS_WEAK


REPORT_KEYWORDS: frozenset = frozenset({
    # Core reporting terms
    "report",
    "reports",
    "reporting",
    "reported",
    "report this",
    "report user",
    "report message",
    "file a report",
    "submit a report",
    "issue",
    "proof",
    # Suspicious activity
    "suspicious",
    "suspect",
    "suspected",
    "sus",
    "sketchy",
    "dodgy",
    "shady",
    "fishy",
    "questionable",
    "concerning behavior",
    
    # Flagging
    "flag",
    "flags",
    "flagged",
    "flagging",
    "red flag",
    "raise a flag",
    
    # Rule violations
    "violating",
    "violates",
    "violation",
    "broke the rules",
    "breaks rules",
    "breaking rules",
    "against the rules",
    "rule break",
    "rule breaker",
    "rules broken",
    
    # Policy violations
    "against policy",
    "policy violation",
    "terms violation",
    "tos violation",
    "violates tos",
    "breaks tos",
    "against tos",
    "terms of service",
    "community guidelines",
    "guidelines violation",
    "against guidelines",
    
    # Harassment
    "harassment",
    "harassing",
    "harass",
    "harassed",
    "bullying",
    "bully",
    "bullied",
    "targeted harassment",
    "targeted",
    "being targeted",
    
    # Spam
    "spam",
    "spamming",
    "spammer",
    "bot spam",
    "ad spam",
    "promotion spam",
    "excessive spam",
    "mass ping",
    "mass mention",
    
    # Toxicity
    "toxic",
    "toxicity",
    "hate speech",
    "hateful",
    "discrimination",
    "discriminatory",
    "racist",
    "racism",
    "sexist",
    "sexism",
    "homophobic",
    "homophobia",
    "transphobic",
    "transphobia",
    "xenophobic",
    "xenophobia",
    
    # Threats
    "threat",
    "threats",
    "threatening",
    "threatened",
    "violent",
    "violence",
    "harm",
    "hurting",
    "endanger",
    "endangering",
    
    # Doxxing/privacy
    "doxx",
    "doxxing",
    "doxed",
    "personal info",
    "private info",
    "private information",
    "personal information",
    "dox threat",
    "sharing private",
    
    # NSFW/Inappropriate
    "nsfw",
    "explicit",
    "inappropriate",
    "unsuitable",
    "nsfl",
    "gore",
    "graphic content",
    "adult content",
    "18+ content",
    
    # Impersonation
    "impersonating",
    "impersonation",
    "impersonate",
    "pretending to be",
    "fake account",
    "copying",
    "pretending",
    
    # Scams/fraud
    "scam",
    "scamming",
    "scammer",
    "fraud",
    "fraudulent",
    "phishing",
    "phish",
    "fake giveaway",
    "fake give away",
    "fake promo",
    "fake promotion",
    
    # Malicious content
    "malware",
    "virus",
    "malicious",
    "malicious link",
    "dangerous link",
    "unsafe link",
    "suspicious link",
    "phishing link",
    
    # Raiding
    "raid",
    "raiding",
    "raider",
    "raiders",
    "raiding server",
    "raiding channel",
    "brigading",
    "brigade",
    
    # Alt/ban evasion
    "ban evasion",
    "evading ban",
    "evading",
    "alt account",
    "sock puppet",
    "sockpuppet",
    "alternate account",
    
    # Specific bad behaviors
    "trolling",
    "troll",
    "trolling behavior",
    "griefing",
    "grief",
    "sealioning",
    "concern trolling",
    "bad faith",
    "bad actor",
    
    # Channel misuse
    "channel hijack",
    "hijacking",
    "channel takeover",
    "spam pings",
    "mass @",
    "mass ping",
    "spam mentions",
    
    # User reports
    "user report",
    "member report",
    "report member",
    "report user",
    "ban appeal",  # Note: might need context
    "appeal ban",  # Note: might need context
    
    # Emergency/Alerts (use with caution)
    "emergency",
    "urgent",
    "urgent report",
    "immediate attention",
    "needs mod",
    "moderator needed",
    "admin needed",
    
    # Evasion/avoidance
    "circumventing",
    "circumvention",
    "bypassing",
    "bypass",
    "avoiding mods",
    
    # Self-harm (critical)
    "self harm",
    "self harm threat",
    "suicide threat",
    "suicidal",
})

# Pre-compiled for report matching as well
REPORT_RE: re.Pattern = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(REPORT_KEYWORDS, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)

MODERATION_COMMANDS: frozenset = frozenset({
    "ban", "kick", "timeout", "warn", "mute", "unmute",
    "lock", "unlock", "slowmode", "purge", "clear",
    "softban", "unban", "strike",
})

# Pre-compiled for mod-embed parsing — use word boundaries to avoid matching
# "ban" inside "abandon", "lock" inside "unlock" text, "warn" inside "I warned"
MODERATION_COMMAND_RE: re.Pattern = re.compile(
    r"(?<!\w)(?:ban(?:ned)?|kick(?:ed)?|warn(?:ed|ing)?|muted?|timeout(?:ed)?|"
    r"unmuted?|unbanned?|softbanned?|strike|purge[d]?|clear(?:ed)?|"
    r"lock(?:ed)?|unlock(?:ed)?|slowmode)(?!\w)",
    re.IGNORECASE,
)

# Question detection pattern — matches messages that are genuine questions.
# Plain `"?" in content` is too broad: it fires on item lists ("option1?"),
# emoji shortcodes (":thinking:?"), and stray punctuation.
# We require the "?" to follow at least one word character or end a sentence.
QUESTION_RE: re.Pattern = re.compile(
    r"(?:\w.*?\?|^.{10,}\?)",   # word chars before ?, and message ≥10 chars
    re.DOTALL,
)

# Keywords that suggest a non-staff message is a help request (for question tracking)
QUESTION_KEYWORDS: frozenset = frozenset({
    "how do", "how can", "how to", "can someone", "anyone know",
    "does anyone", "is there a way", "help me", "help with",
    "not working", "broken", "error", "stuck", "confused",
})

# ─── Engagement keyword sets ──────────────────────────────────────────────────

# Social greeting signals — distinct from help keywords (these are warmth/welcome,
# not problem-solving).  Used to detect New Member Greeting events.
GREETING_KEYWORDS: frozenset = frozenset({
    # Core welcomes
    "welcome",
    "welcomes",
    "welcoming",
    "welcome aboard",
    "welcome to the server",
    "welcome to the community",
    
    # Happy to have you
    "glad you're here",
    "glad you joined",
    "happy to have you",
    "happy you're here",
    "happy you joined",
    "so glad you're here",
    "so glad you joined",
    "thrilled to have you",
    "excited to have you",
    "excited you're here",
    
    # Nice to meet you
    "nice to meet",
    "nice to meet you",
    "pleasure to meet",
    "lovely to meet",
    "great to meet",
    "good to meet",
    
    # Good to see you
    "good to see you",
    "great to see you",
    "awesome to see you",
    "wonderful to see you",
    "always good to see",
    
    # Getting to know you
    "what are you into",
    "what are your interests",
    "what do you enjoy",
    "what brings you here",
    "what brought you here",
    "how did you find us",
    "where are you from",
    "where in the world",
    "what timezone",
    "what's your timezone",
    "how long have you",
    "been here before",
    "first time here",
    
    # Make yourself at home
    "enjoy your stay",
    "hope you enjoy",
    "hope you like it",
    "hope you have fun",
    "hope you love it",
    "make yourself at home",
    "feel at home",
    "settle in",
    "get comfortable",
    
    # Great to have you
    "great to have you",
    "awesome to have you",
    "amazing to have you",
    "fantastic to have you",
    "wonderful to have you",
    "happy you found us",
    "glad you found us",
    "glad you discovered",
    
    # Feel free to ask
    "feel free to ask",
    "don't hesitate to ask",
    "let us know if",
    "reach out if",
    "any questions just",
    "if you need anything",
    "if you need help",
    "if you have questions",
    
    # Introductions
    "introduce yourself",
    "tell us about yourself",
    "tell us a bit",
    "share a bit",
    "tell us about you",
    "introduce when you're ready",
    "feel free to introduce",
    "take a moment to introduce",
    
    # Community warmth
    "we're happy you're here",
    "we're glad you're here",
    "we're excited you're here",
    "you're part of the community",
    "you're part of the family",
    "glad to have you with us",
    "happy to have you with us",
    
    # Engagement questions
    "what games do you play",
    "what do you do for fun",
    "what are your hobbies",
    "any hobbies",
    "interests outside",
    "what brings you joy",
    
    # Enthusiasm
    "woohoo",
    "w00t",
    "yay",
    "awesome",
    "amazing",
    "fantastic",
})

CONVERSATION_STARTER_KEYWORDS: frozenset = frozenset({
    # Asking opinions
    "what do you think",
    "what do you all think",
    "what does everyone think",
    "what are your thoughts",
    "any thoughts on",
    "opinions on",
    "your take on",
    "what's your take",
    "how do you feel about",
    "what's your opinion",
    
    # Anyone/anybody
    "anyone here",
    "anyone around",
    "anybody here",
    "anybody around",
    "anyone interested",
    "anybody interested",
    "anyone want to",
    "anybody want to",
    "anyone else",
    "anybody else",
    "anyone up for",
    "anybody up for",
    
    # Hot takes and opinions
    "hot take",
    "unpopular opinion",
    "controversial opinion",
    "am I the only one",
    "does anyone else",
    "who else thinks",
    "who agrees",
    "am I wrong for thinking",
    
    # Polls and questions
    "poll:",
    "quick poll",
    "strawpoll",
    "vote:",
    "voting:",
    "question of the day",
    "qotd",
    "discussion question",
    "debate:",
    
    # What's everyone
    "what's everyone",
    "what is everyone",
    "what are everyone",
    "what are you all",
    "what's everyone up to",
    "what is everyone up to",
    "what are you all up to",
    "what's everyone doing",
    "what is everyone doing",
    "what are you all doing",
    "what's everyone playing",
    "what is everyone playing",
    "what are you all playing",
    "what's everyone watching",
    "what is everyone watching",
    "what are you all watching",
    "what's everyone reading",
    "what is everyone reading",
    "what are you all reading",
    
    # Who here
    "who here",
    "who's here",
    "who else",
    "who all",
    "who's playing",
    "who's watching",
    "who's interested",
    "who's down for",
    "who wants to",
    
    # Would you rather
    "would you rather",
    "wyrd",
    "wyr",
    "would you prefer",
    "which would you choose",
    "pick one:",
    "choose one:",
    
    # What's your
    "what's your",
    "what is your",
    "what are your",
    "what's your favorite",
    "what is your favorite",
    "what are your favorites",
    "what's your go-to",
    "what is your go-to",
    "what's your opinion on",
    "what is your opinion on",
    
    # Rate/review
    "rate this",
    "rate it",
    "review this",
    "what would you rate",
    "out of ten",
    "/10",
    "scale of 1 to 10",
    "on a scale",
    
    # Game time
    "game time",
    "let's play a game",
    "time for a game",
    "mini game",
    "word game",
    "guess the",
    "never have I ever",
    "two truths one lie",
    "this or that",
    
    # Fun facts
    "fun fact",
    "fun facts",
    "did you know",
    "interesting fact",
    "today I learned",
    "til",
    "random fact",
    "here's a fact",
    
    # Discussion starters
    "let's talk about",
    "lets talk about",
    "let's discuss",
    "lets discuss",
    "discussion:",
    "topic:",
    "subject:",
    "speaking of",
    "on the topic of",
    "regarding",
    
    # Hypotheticals
    "what if",
    "imagine if",
    "suppose",
    "hypothetically",
    "if you could",
    "if you had to",
    "if you were",
    
    # Recommendations
    "recommend me",
    "suggestions for",
    "any recommendations",
    "what should I",
    "what do you recommend",
    "looking for recommendations",
    
    # Weekend/plans
    "weekend plans",
    "plans for the weekend",
    "any plans for",
    "what are you doing this weekend",
    "what's everyone doing this weekend",
    "anyone doing anything fun",
    
    # Media discussions
    "just watched",
    "just finished",
    "just started",
    "currently watching",
    "currently playing",
    "currently reading",
    "binging",
    "binge watching",
    "anyone else watching",
    "anyone else playing",
    "anyone else read",
    
    # Random/icebreakers
    "random question",
    "icebreaker",
    "get to know you",
    "getting to know",
    "would you rather",
    "favorite thing",
    "what's something",
    
    # Excitement/events
    "excited for",
    "looking forward to",
    "hyped for",
    "countdown to",
    "who's ready for",
    
    # Help/advice
    "need advice",
    "looking for advice",
    "seeking recommendations",
    "help me decide",
    "what would you do",
})

GREETING_RE: re.Pattern           = _build_kw_pattern(GREETING_KEYWORDS)
CONVERSATION_STARTER_RE: re.Pattern = _build_kw_pattern(CONVERSATION_STARTER_KEYWORDS)

# ─── Moderation severity weights ─────────────────────────────────────────────
MODERATION_SEVERITY: dict = {
    "ban":       3.0,
    "softban":   2.5,
    "kick":      2.0,
    "timeout":   1.5,
    "mute":      1.5,
    "warn":      1.0,
    "strike":    1.0,
    "purge":     1.5,
    "clear":     1.5,
    "lock":      0.5,
    "unlock":    0.3,
    "slowmode":  0.4,
    "unmute":    0.4,
    "unban":     0.5,
}

# ─── Quality signal multipliers ───────────────────────────────────────────────
QUALITY_SIGNALS: dict = {
    "code_block":    2.5,    # ```…``` — technical help
    "inline_code":   0.6,    # `…` — referencing commands/values
    "link":          1.8,    # http link — pointing to resources
    "numbered_list": 1.2,    # 1. / 2. — structured answer
    "bullet_list":   0.8,    # - / • — organised points
    "long_message":  1.0,    # > QUALITY_MIN_WORDS * 2
    "image":         1.5,    # message attachment
    "quote":         0.5,    # > prefix — quoting for clarity
}

# Minimum raw score before a QualityEvent is recorded.
# Raised from 1.0 → 1.5 so that lone inline-code snippets don't qualify.
QUALITY_SCORE_THRESHOLD = 1.5

# ─── Hours / coverage ────────────────────────────────────────────────────────
# BUG FIX: range(16, 00) == range(16, 0) which is an EMPTY range because 0 < 16.
# peak_hours_bonus was therefore NEVER awarded to anyone.
# Corrected to 2 PM – 10 PM (UTC-based; adjust via env override if needed).
PEAK_HOURS:     range = range(14, 23)   # 2 PM – 10 PM inclusive

# Off-peak: midnight–8 AM + 10 PM–midnight
OFF_PEAK_HOURS: list  = list(range(0, 8)) + list(range(22, 24))

# Maximum help events credited per (staff_id, helped_id) pair per day.
# Prevents a staff member gaming the metric by repeatedly replying to the same person.
MAX_HELP_EVENTS_PER_USER_PER_DAY = 3

# Maximum reactions credited per staff member per day (anti-spam).
MAX_REACTIONS_PER_DAY = 50

# Maximum report credits per staff member per hour (anti-spam).
MAX_REPORTS_PER_HOUR = 10

# Maximum greeting credits per (staff, new-member) pair per day.
MAX_GREETINGS_PER_USER_PER_DAY = 1

# Maximum conversation-starter credits per staff member per day.
MAX_STARTERS_PER_DAY = 10

# Maximum reactions-received credits per message (cap to stop viral gaming).
MAX_REACTIONS_RECEIVED_PER_MSG = 10

# Seconds a channel must be silent before a staff post counts as a revival.
DEAD_CHAT_THRESHOLD = 1800      # 30 min

# Seconds after a staff revival post for a non-staff reply to confirm it.
REVIVAL_RESPONSE_WINDOW = 600   # 10 min

# ─── Score weights (defaults; overridable per guild via DB) ───────────────────
SCORE_WEIGHTS: dict = {
    # ── Community ──
    "unique_members":        2.0,
    "help_events":           3.0,
    "proactive_help":        3.0,
    "new_member_helped":     5.0,
    "reactions_given":       0.5,
    "threads_created":       1.5,
    "new_member_chat":       1.5,  
    # ── Moderation ──
    "moderation_actions":    2.0,
    "moderation_severity":   2.0,
    "reports":               3.0,
    # ── Presence ──
    "vc_hours":              1.5,
    "conversation_sessions": 1.5,
    "server_bumps":          3.0,
    # ── Quality & Speed ──
    "fast_response":         1.5,
    "first_response":        3.0,
    "message_quality":       0.12,
    "quality_event":         1.5,
    # ── Consistency ──
    "daily_activity":        5.0,
    "hourly_spread":         3.0,
    "peak_hours_bonus":      2.5,
    "off_peak_coverage":     3.5,
    "streak_bonus":          7.0,
    # ── Engagement (community building) ──
    "dead_chat_revival":     6.0,   # revived a silent channel and got a response
    "conversation_starter":  4.5,   # initiated a discussion in general channels
    "new_member_greeting":   3.0,   # social welcome to a new member (non-help)
    "reactions_received":    0.3,   # community reacted to staff's message
}

# ─── KPI Targets (per 30 days) — used for progress display ───────────────────
KPI_TARGETS: dict = {
    "help_events":          50,
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
    # Engagement KPIs
    "dead_chat_revivals":    5,
    "conversation_starters": 10,
    "new_member_greetings":  15,
    "reactions_received":    30,
}

# ─── Rank tier thresholds (30-day engagement score) ───────────────────────────
RANK_TIERS: dict = {
    "Diamond":  {"min": 2001, "emoji": "💎", "colour": Colour.from_str("#00CFFF"), "label": "Elite"},
    "Platinum": {"min": 1001, "emoji": "🌟", "colour": Colour.from_str("#E8E8FF"), "label": "Expert"},
    "Gold":     {"min":  501, "emoji": "🥇", "colour": Colour.gold(),              "label": "Proficient"},
    "Silver":   {"min":  201, "emoji": "🥈", "colour": Colour.light_grey(),        "label": "Developing"},
    "Bronze":   {"min":    0, "emoji": "🥉", "colour": Colour.from_str("#CD7F32"), "label": "Newcomer"},
}

# ─── Achievement definitions ──────────────────────────────────────────────────
ACHIEVEMENT_THRESHOLDS: dict = {
    "first_blood":      {"metric": "help_events",           "value": 1,    "emoji": "🩸",  "desc": "First help event"},
    "helper":           {"metric": "help_events",           "value": 50,   "emoji": "🤝",  "desc": "50 help events"},
    "sensei":           {"metric": "help_events",           "value": 250,  "emoji": "🧑‍🏫", "desc": "250 help events"},
    "guardian":         {"metric": "moderation_actions",    "value": 25,   "emoji": "🛡️",  "desc": "25 mod actions"},
    "enforcer":         {"metric": "moderation_actions",    "value": 100,  "emoji": "⚖️",  "desc": "100 mod actions"},
    "voice_of_reason":  {"metric": "vc_hours",              "value": 10.0, "emoji": "🎙️",  "desc": "10 VC hours"},
    "broadcaster":      {"metric": "vc_hours",              "value": 50.0, "emoji": "📡",  "desc": "50 VC hours"},
    "always_on":        {"metric": "current_streak",        "value": 7,    "emoji": "🔥",  "desc": "7-day streak"},
    "unstoppable":      {"metric": "current_streak",        "value": 30,   "emoji": "⚡",  "desc": "30-day streak"},
    "onboarding_hero":  {"metric": "new_members_helped",    "value": 20,   "emoji": "🌱",  "desc": "20 new members helped"},
    "speed_demon":      {"metric": "fast_responses",        "value": 25,   "emoji": "💨",  "desc": "25 fast responses"},
    "first_responder":  {"metric": "first_responses",       "value": 20,   "emoji": "🚀",  "desc": "20 first-to-respond"},
    "thread_master":    {"metric": "threads_created",       "value": 15,   "emoji": "🧵",  "desc": "15 threads created"},
    "reactor":          {"metric": "reactions_given",       "value": 100,  "emoji": "😊",  "desc": "100 reactions given"},
    "night_owl":        {"metric": "off_peak_messages",     "value": 50,   "emoji": "🦉",  "desc": "50 off-peak messages"},
    # Engagement achievements
    "conversationalist":{"metric": "conversation_starters", "value": 10,   "emoji": "💡",  "desc": "10 conversations started"},
    "social_butterfly": {"metric": "new_member_greetings",  "value": 25,   "emoji": "🦋",  "desc": "25 new members greeted"},
    "reviver":          {"metric": "dead_chat_revivals",     "value": 5,    "emoji": "🔋",  "desc": "5 dead chats revived"},
    "crowd_pleaser":    {"metric": "reactions_received",     "value": 50,   "emoji": "🌟",  "desc": "50 reactions received"},
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

# BUG FIX: MSG_QUALITY_WORD_THRESHOLD was a legacy duplicate of QUALITY_MIN_WORDS
# with a different value (5 vs 8), silently used in older code paths.
# Unified — use QUALITY_MIN_WORDS everywhere.
MSG_QUALITY_WORD_THRESHOLD = QUALITY_MIN_WORDS   # alias kept for any legacy imports

# ─── Display constants ────────────────────────────────────────────────────────
HEATMAP_CHARS  = ("░", "▒", "▓", "█")
PROGRESS_CHARS = ("░", "▒", "▓", "█")
MEDAL_EMOJIS   = ("🥇", "🥈", "🥉")

LEADERBOARD_LIMIT = 15