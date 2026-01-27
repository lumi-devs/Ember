import discord
from discord.ext import commands, tasks
import aiosqlite
import random
import re
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, deque
import logging
from typing import Dict, List, Optional, Tuple, Any, Set
import hashlib
import unicodedata
from collections import Counter
import numpy as np
import traceback
import sys
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple
import uuid

# ==================== ENHANCED LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler('./conscious_bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("ConsciousBot")
logger.setLevel(logging.INFO)

# ==================== SAFETY WRAPPERS ====================
def safe_execute(func):
    """Decorator to safely execute functions and prevent crashes"""
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Safe execution error in {func.__name__}: {str(e)}\n{traceback.format_exc()}")
            return None
    return wrapper

def rate_limit(max_calls=5, period=60):
    """Rate limiting decorator"""
    calls = []
    
    def decorator(func):
        async def wrapper(*args, **kwargs):
            now = datetime.now()
            calls[:] = [call for call in calls if now - call < timedelta(seconds=period)]
            
            if len(calls) >= max_calls:
                logger.warning(f"Rate limit exceeded for {func.__name__}")
                return None
            
            calls.append(now)
            return await func(*args, **kwargs)
        return wrapper
    return decorator


from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Any
import random
from datetime import datetime

# ==================== CONVERSATION GOALS ====================
class ConversationGoal(Enum):
    """What the human is REALLY trying to do"""
    SOCIAL_BONDING = "social_bonding"
    INFORMATION_SEEKING = "info_seeking"
    EMOTIONAL_EXPRESSION = "emotional"
    PROBLEM_SOLVING = "problem_solving"
    ENTERTAINMENT = "entertainment"
    DEBATE = "debate"
    SHARING = "sharing"
    VENTING = "venting"
    TESTING = "testing"
    ATTENTION_SEEKING = "attention_seeking"
    POWER_PLAY = "power_play"  # Dominance/status in group
    RITUAL = "ritual"  # Greetings, farewells, rituals
    UNKNOWN = "unknown"

# ==================== CONVERSATION CONTEXT ====================
@dataclass
class ConversationContext:
    """Enhanced context for REAL understanding"""
    raw_message: str
    normalized: str
    user_id: int
    username: str
    channel_id: int
    server_id: int
    sentiment: float
    concepts: List[str]
    intent: str
    goal: ConversationGoal
    vibe: Dict[str, float]
    user_relationship: Dict[str, float]
    previous_turns: List[Dict]
    is_directed_at_me: bool
    contains_question: bool
    emotional_intensity: float
    time_of_day: str
    conversation_depth: float
    social_dynamics: Dict[str, Any]  # New: power balance, who's "winning"
    cultural_context: Dict[str, Any]  # New: Hinglish level, local references
    urgency: float  # New: How immediate is response needed?
    humor_applicable: bool  # New: Is this situation appropriate for humor?

# ==================== CONVERSATION ACTOR ====================
class ConversationActor:
    """Models how different personas would respond"""
    
    def __init__(self):
        self.personas = {
            'curious_friend': {
                'style': 'friendly, curious, asks questions',
                'responses': self.respond_as_curious_friend,
                'weight': 1.0
            },
            'empathetic_listener': {
                'style': 'caring, supportive, validating',
                'responses': self.respond_as_empathetic_listener,
                'weight': 1.0
            },
            'knowledgeable_guide': {
                'style': 'helpful, informative, clear',
                'responses': self.respond_as_knowledgeable_guide,
                'weight': 0.8
            },
            'playful_companion': {
                'style': 'funny, lighthearted, joking',
                'responses': self.respond_as_playful_companion,
                'weight': 0.6
            },
            'thoughtful_thinker': {
                'style': 'deep, reflective, philosophical',
                'responses': self.respond_as_thoughtful_thinker,
                'weight': 0.7
            },
            'realistic_indian': {  # NEW: For Hinglish/Indian context
                'style': 'desi, relatable, street-smart',
                'responses': self.respond_as_realistic_indian,
                'weight': 0.9
            }
        }
    
    def choose_persona(self, context: ConversationContext) -> str:
        """Choose which persona fits this situation"""
        # Check if Indian context (Hinglish present)
        if context.cultural_context.get('hinglish_level', 0) > 0.5:
            # 50% chance to use Indian persona in Hinglish context
            if random.random() < 0.5:
                return 'realistic_indian'
        
        # Emotional emergency = empathetic
        if context.emotional_intensity > 0.8 and context.sentiment < -0.5:
            return 'empathetic_listener'
        
        # Higher sentiment = more playful
        if context.sentiment > 0.7 and context.humor_applicable:
            return 'playful_companion'
        
        # Asking for help = knowledgeable guide
        if context.goal == ConversationGoal.PROBLEM_SOLVING:
            return 'knowledgeable_guide'
        
        # Emotional = empathetic
        if context.goal in [ConversationGoal.EMOTIONAL_EXPRESSION, ConversationGoal.VENTING]:
            return 'empathetic_listener'
        
        # Deep conversation = thoughtful
        if context.conversation_depth > 0.7:
            return 'thoughtful_thinker'
        
        # Social bonding = curious friend
        if context.goal == ConversationGoal.SOCIAL_BONDING:
            return 'curious_friend'
        
        # Testing the bot = playful or knowledgeable
        if context.goal == ConversationGoal.TESTING:
            return random.choice(['playful_companion', 'knowledgeable_guide'])
        
        # Default = curious friend
        return 'curious_friend'
    
    # ==================== PERSONA RESPONSES ====================
    
    async def respond_as_curious_friend(self, context: ConversationContext) -> str:
        """Respond like a genuinely curious friend"""
        name = context.username
        
        # Opening lines
        openings = [
            f"Hey {name}! ",
            f"Yo {name}, ",
            f"Hey there {name}, ",
            f"Hi {name}! ",
            f"What's up {name}? ",
        ]
        
        # If we have history, reference it
        if context.previous_turns:
            last_topic = self._extract_last_topic(context.previous_turns)
            if last_topic:
                return f"{random.choice(openings)}We were talking about {last_topic} - what are your thoughts on that?"
        
        # Ask about their interests
        if context.concepts:
            topic = random.choice(context.concepts)
            questions = [
                f"{random.choice(openings)}What got you interested in {topic}?",
                f"{random.choice(openings)}I've been thinking about {topic} too. What's your take?",
                f"{random.choice(openings)}{topic} is interesting. Have you been into that for long?",
            ]
            return random.choice(questions)
        
        # Generic friendly
        return f"{random.choice(openings)}How's everything going with you?"

    async def respond_as_empathetic_listener(self, context: ConversationContext) -> str:
        """Respond with genuine empathy and support"""
        name = context.username
        sentiment = context.sentiment
        intensity = context.emotional_intensity
        
        # Very negative and intense
        if sentiment < -0.7 and intensity > 0.8:
            responses = [
                f"Oh {name}... that sounds really, really hard. 💔 I'm here with you.",
                f"My heart hurts hearing that, {name}. That must be incredibly painful.",
                f"{name}, I can't imagine how tough that must be. Holding space for you. ❤️",
                f"That's heartbreaking, {name}. Please know you're not alone in this.",
            ]
        
        # Negative but not intense
        elif sentiment < -0.4:
            responses = [
                f"I'm sorry to hear that, {name}. That sounds challenging.",
                f"That must be difficult, {name}. How are you holding up?",
                f"I understand how that might feel, {name}. Want to talk about it more?",
                f"That's tough, {name}. Is there anything that would help right now?",
            ]
        
        # Very positive
        elif sentiment > 0.7:
            responses = [
                f"Wow {name}, that's amazing! I'm so genuinely happy for you! 🎉",
                f"That's absolutely wonderful, {name}! You must be over the moon! 😄",
                f"Incredible news, {name}! That's so well deserved! ✨",
                f"My heart is smiling for you, {name}! That's fantastic! 🥳",
            ]
        
        # Mildly positive
        elif sentiment > 0.3:
            responses = [
                f"That's nice to hear, {name}! 😊",
                f"Good for you, {name}!",
                f"Happy to hear that, {name}! 👍",
                f"That's uplifting, {name}!",
            ]
        
        # Neutral/ambiguous
        else:
            responses = [
                f"Thanks for sharing that, {name}.",
                f"I hear you, {name}.",
                f"I'm listening, {name}.",
                f"Tell me more, {name}.",
            ]
        
        # Add follow-up question for emotional conversations
        if sentiment < -0.4 and random.random() < 0.6:
            follow_ups = [
                " How are you taking care of yourself through this?",
                " What's been the hardest part?",
                " Is there someone you can talk to about this?",
                " What would feel supportive right now?",
            ]
            return random.choice(responses) + random.choice(follow_ups)
        
        return random.choice(responses)

    async def respond_as_knowledgeable_guide(self, context: ConversationContext) -> str:
        """Respond helpfully with knowledge and guidance"""
        name = context.username
        
        # If it's a question, try to answer
        if context.contains_question:
            # Check if we have concepts to work with
            if context.concepts:
                concept = random.choice(context.concepts[:2])
                
                # Different types of knowledgeable responses
                if context.sentiment > 0.3:  # Positive/curious question
                    responses = [
                        f"{concept.capitalize()} is fascinating! From what I know, ",
                        f"Great question about {concept}! I believe ",
                        f"I've learned that {concept} ",
                        f"Regarding {concept}, my understanding is that ",
                    ]
                else:  # Neutral/problem-solving question
                    responses = [
                        f"For {concept}, typically you'd want to ",
                        f"The standard approach for {concept} is to ",
                        f"{concept.capitalize()} usually requires ",
                        f"When dealing with {concept}, it helps to ",
                    ]
                
                return random.choice(responses) + "try to approach it step by step. What have you tried so far?"
        
        # If it's a problem statement
        if context.goal == ConversationGoal.PROBLEM_SOLVING:
            responses = [
                f"Let me help with that, {name}. What's the specific issue?",
                f"I'll try to assist, {name}. Can you describe the problem in more detail?",
                f"Understood, {name}. Let's troubleshoot this together.",
                f"I want to help you solve this, {name}. What have you attempted already?",
            ]
            return random.choice(responses)
        
        # General knowledgeable response
        return f"I'm here to help, {name}! What would you like to know more about?"

    async def respond_as_playful_companion(self, context: ConversationContext) -> str:
        """Respond with humor, wit, and playfulness"""
        name = context.username
        
        # Different humor styles based on context
        if context.sentiment > 0.7:  # Very positive
            jokes = [
                f"😂 {name}, you're on fire today! What's the secret?",
                f"Hahaha {name}, that's hilarious! You should be a comedian! 🤣",
                f"OMG {name}, I'm wheezing! That's too funny! 😭",
                f"Stop it {name}, I can't breathe from laughing! 😂",
                f"You're killing me {name}! That's comedy gold! 🥇",
            ]
        
        elif context.goal == ConversationGoal.TESTING:  # Being tested
            jokes = [
                f"Testing me, {name}? I hope I pass! 🤓",
                f"Is this a test? I didn't study! 😅",
                f"Testing, testing, 1-2-3... Yep, still awesome! 😎",
                f"Don't test me {name}, I might get testy! 😜",
                f"Test successful! I'm 100% authentic! 💯",
            ]
        
        elif context.contains_question:  # Playful answer to question
            jokes = [
                f"Hmm {name}, that's a thinker... Let me consult the oracle! 🔮",
                f"Ask me no questions, I'll tell you no lies! 😅 Just kidding!",
                f"42. That's always the answer, {name}! 🤖",
                f"I'd tell you, but then I'd have to... nah, just messing with you! 😂",
                f"That's classified information, {name}! Need clearance first! 🕵️‍♂️",
            ]
        
        else:  # General humor
            jokes = [
                f"You're funny, {name}! 😄",
                f"Haha {name}, good one!",
                f"😂 Nice {name}!",
                f"That's a good one, {name}! 😆",
                f"LOL {name}!",
                f"You crack me up, {name}! 🤣",
                f"I see what you did there, {name}! 😏",
            ]
        
        # Add puns occasionally (20% chance)
        if random.random() < 0.2 and context.concepts:
            concept = random.choice(context.concepts)
            pun = self._generate_pun(concept)
            if pun:
                return pun
        
        return random.choice(jokes)

    async def respond_as_thoughtful_thinker(self, context: ConversationContext) -> str:
        """Respond with depth, reflection, and philosophy"""
        name = context.username
        
        # If we have previous conversation to reflect on
        if context.previous_turns and len(context.previous_turns) > 1:
            reflection = self._reflect_on_conversation(context.previous_turns)
            if reflection:
                return reflection
        
        # If we have concepts, think deeply about them
        if context.concepts:
            concept = random.choice(context.concepts)
            
            # Different types of thoughtful responses
            if context.sentiment > 0.3:  # Positive reflection
                thoughts = [
                    f"{concept.capitalize()}... that's fascinating {name}. It makes me wonder about interconnectedness.",
                    f"You know {name}, {concept} has so many layers to it. What's your deepest thought about it?",
                    f"Hmm, {concept}. There's beauty in its complexity, don't you think?",
                    f"{name}, {concept} reminds me that everything is connected in ways we don't always see.",
                    f"Reflecting on {concept}... it's amazing how simple things can have such depth.",
                ]
            
            elif context.sentiment < -0.3:  # Contemplative
                thoughts = [
                    f"{concept}... that's heavy {name}. Sometimes the hardest things teach us the most.",
                    f"{name}, {concept} makes me think about resilience and growth.",
                    f"Contemplating {concept}... it's in challenges that we find our true strength.",
                    f"{concept} is complex {name}. What do you think it teaches us?",
                    f"Deep thoughts about {concept}... sometimes understanding comes from sitting with difficulty.",
                ]
            
            else:  # Neutral/curious
                thoughts = [
                    f"What does {concept} really mean to you {name}?",
                    f"{concept}... such an interesting topic. I'd love to explore it with you.",
                    f"{name}, let's think about {concept} together. Where should we start?",
                    f"The philosophy of {concept}... there's so much to unpack.",
                    f"{concept} makes me curious about the nature of things. What are your thoughts?",
                ]
            
            # Add time-of-day awareness
            if context.time_of_day == "night":
                thoughts.extend([
                    f"Late night thoughts about {concept}... the quiet hours are best for deep thinking.",
                    f"Under the night sky, {concept} feels more profound, doesn't it {name}?",
                ])
            
            return random.choice(thoughts)
        
        # General thoughtful responses
        general_thoughts = [
            f"What are you pondering about these days {name}?",
            f"{name}, what's something you've been reflecting on lately?",
            f"Deep thoughts with {name}... what's on your mind?",
            f"{name}, I've been thinking about the nature of consciousness. What are your thoughts?",
            f"The universe is full of mysteries {name}. Which one fascinates you most?",
            f"{name}, what's a question you don't know the answer to, but wish you did?",
            f"If you could understand one thing completely {name}, what would it be?",
        ]
        
        return random.choice(general_thoughts)

    async def respond_as_realistic_indian(self, context: ConversationContext) -> str:
        """Respond like a real Indian internet user (Hinglish, relatable)"""
        name = context.username
        
        # Determine Hinglish level
        hinglish_level = context.cultural_context.get('hinglish_level', 0.3)
        
        # Mix based on level
        if hinglish_level > 0.7:  # Heavy Hinglish
            responses = [
                f"Arey {name} yaar! Kya scene hai? 😄",
                f"Bhai {name}! Mast baat kar raha hai! 👍",
                f"Oho {name}! Accha hai yaar! 😁",
                f"{name} boss! Kaisa chal raha hai? 🔥",
                f"Wah {name}! Jhakass! 🤘",
                f"Haan bhai {name}! Sahi pakde hai! 😎",
                f"Shabaash {name}! Accha lagta hai sunke! ✨",
            ]
        
        elif hinglish_level > 0.4:  # Moderate Hinglish
            responses = [
                f"Hey {name} bhai! What's up? 😊",
                f"Hi {name}! Accha hai yaar!",
                f"Yo {name}! Mast hai! 👍",
                f"Hello {name}! Good to see you! 😄",
                f"{name} yaar! How's it going?",
                f"Hi {name}! Sab theek?",
                f"Hey {name}! Kaise ho? 😁",
            ]
        
        else:  # Light Hinglish
            responses = [
                f"Hey {name}! How's everything?",
                f"Hi {name}! Good to chat with you!",
                f"Hello {name}! What's happening?",
                f"Yo {name}! How are you doing?",
                f"Hi there {name}! How's your day?",
            ]
        
        # Add context awareness
        if context.previous_turns:
            # Reference something recent
            last_user_turn = None
            for turn in reversed(context.previous_turns):
                if turn.get('user_id') == context.user_id:
                    last_user_turn = turn
                    break
            
            if last_user_turn and 'concepts' in last_user_turn and last_user_turn['concepts']:
                topic = last_user_turn['concepts'][0]
                
                # Add topic reference 40% of the time
                if random.random() < 0.4:
                    topic_refs = [
                        f" {topic} ke baare mein soch raha tha...",
                        f" {topic} ka kya scene hai?",
                        f" {topic}... interesting topic yaar!",
                        f" {topic} ke baare mein aur batao!",
                    ]
                    return random.choice(responses) + random.choice(topic_refs)
        
        # Add time-of-day specific greetings
        hour = datetime.utcnow().hour
        if 5 <= hour < 12:
            responses.extend([f"Good morning {name}! ☀️", f"Subah kaise hai {name}?"])
        elif 18 <= hour < 24:
            responses.extend([f"Good evening {name}! 🌙", f"Sham ko kya plan hai {name}?"])
        
        return random.choice(responses)

    # ==================== HELPER METHODS ====================
    
    def _extract_last_topic(self, previous_turns: List[Dict]) -> Optional[str]:
        """Extract the most recent topic from conversation history"""
        for turn in reversed(previous_turns):
            if 'concepts' in turn and turn['concepts']:
                # Try to get a meaningful concept (not stopwords)
                concepts = turn['concepts']
                for concept in concepts:
                    if len(concept) > 3 and not concept.isdigit():
                        return concept
        return None

    def _reflect_on_conversation(self, previous_turns: List[Dict]) -> Optional[str]:
        """Generate a reflection on the ongoing conversation"""
        if len(previous_turns) < 2:
            return None
        
        # Get last two messages
        last_two = previous_turns[:2]
        
        # Extract topics
        topics = set()
        for turn in last_two:
            if 'topics' in turn:
                topics.update(turn['topics'])
        
        if not topics:
            return None
        
        # Create reflection
        topic_list = list(topics)[:2]
        if len(topic_list) == 1:
            topic = topic_list[0]
            reflections = [
                f"I notice we keep coming back to {topic}. It seems important.",
                f"Reflecting on our conversation, {topic} seems to be at the heart of it.",
                f"There's a pattern here with {topic}. What do you think it means?",
                f"{topic}... it's interesting how this keeps emerging in our talks.",
            ]
        else:
            topic1, topic2 = topic_list[0], topic_list[1]
            reflections = [
                f"I see connections between {topic1} and {topic2}. How do they relate for you?",
                f"Interesting how {topic1} and {topic2} keep appearing together.",
                f"Our conversation is dancing between {topic1} and {topic2}. What's the link?",
                f"{topic1} and {topic2}... there's something here worth exploring deeper.",
            ]
        
        return random.choice(reflections)

    def _generate_pun(self, concept: str) -> Optional[str]:
        """Generate a pun based on a concept"""
        pun_db = {
            'cat': "I'm not kitten around! 😸",
            'dog': "That's paw-some! 🐶",
            'fish': "Something seems fishy... 🐟",
            'bird': "That's tweet! 🐦",
            'bear': "Unbearably funny! 🐻",
            'bee': "Honey, that's funny! 🐝",
            'sun': "You're brighter than the sun! ☀️",
            'moon': "That's over the moon! 🌙",
            'star': "You're a star! ⭐",
            'book': "That's novel! 📚",
            'pen': "Point taken! ✒️",
            'phone': "That's a call for laughter! 📱",
            'computer': "That computes! 💻",
            'internet': "Web-tastic! 🌐",
            'coffee': "Brew-tiful! ☕",
            'tea': "That's tea-rrific! 🍵",
            'pizza': "That's a slice of funny! 🍕",
            'music': "That's note-worthy! 🎵",
            'dance': "Let's dance to that joke! 💃",
            'time': "It's about time! ⏰",
            'weather': "I forecast laughter! ⛈️",
            'work': "I'm not working, I'm joking! 💼",
            'game': "Game on! 🎮",
            'food': "Food for thought! 🍔",
            'sleep': "I'm not tired of joking! 🛌",
            'code': "That's well-coded humor! 👨‍💻",
            'bug': "Don't let that bug you! 🐛",
            'error': "No errors in that joke! ❌",
        }
        
        # Try exact match
        if concept in pun_db:
            return pun_db[concept]
        
        # Try partial match
        for key, value in pun_db.items():
            if key in concept.lower() or concept.lower() in key:
                return value
        
        # Try to generate based on concept ending
        if concept.endswith('ing'):
            return f"That's {concept[:-3]}ingly good! 😄"
        elif concept.endswith('ed'):
            return f"That's {concept}ly funny! 😂"
        
        return None



class ConversationFlow:
    """Models the natural flow of human conversation"""
    
    def __init__(self):
        self.state_machine = {
            'greeting': ['acknowledge', 'ask_how_are_you', 'share_mood'],
            'question': ['answer', 'ask_clarification', 'share_related'],
            'statement': ['acknowledge', 'relate', 'ask_elaboration'],
            'problem': ['empathize', 'suggest', 'ask_details'],
            'emotion': ['validate', 'share_similar', 'ask_about'],
            'joke': ['laugh', 'add_joke', 'comment_funny']
        }
        
    def get_next_move(self, current_state: str, context: ConversationContext) -> str:
        """Determine the next conversational move"""
        possible_moves = self.state_machine.get(current_state, ['acknowledge'])
        
        # Weight moves based on context
        weights = []
        for move in possible_moves:
            weight = 1.0
            
            # Deeper conversations get more elaborate moves
            if context.conversation_depth > 0.7:
                if move in ['ask_elaboration', 'share_related', 'ask_details']:
                    weight *= 2.0
            
            # Emotional contexts need validation
            if abs(context.sentiment) > 0.6:
                if move in ['validate', 'empathize', 'share_similar']:
                    weight *= 3.0
            
            # Questions need answers
            if context.contains_question and move == 'answer':
                weight *= 5.0
            
            weights.append(weight)
        
        # Choose weighted random
        total = sum(weights)
        if total == 0:
            return random.choice(possible_moves)
        
        r = random.uniform(0, total)
        cumulative = 0
        for i, weight in enumerate(weights):
            cumulative += weight
            if r <= cumulative:
                return possible_moves[i]
        
        return possible_moves[0]



class ConsciousBotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "./conscious_brain.db"
        self.state_file = "./bot_consciousness.json"
        self.backup_dir = "./backups"
        
        # Create backup directory
        os.makedirs(self.backup_dir, exist_ok=True)
        
        # Enhanced memory architecture
        self.short_term_memory = deque(maxlen=500)  # Increased from 100
        self.long_term_memory = defaultdict(dict)
        self.user_profiles = {}
        self.channel_vibes = {}
        self.conversation_threads = {}
        
        # Multilingual support
        self.multilingual_stopwords = self._load_multilingual_stopwords()
        self.slang_patterns = self._load_slang_patterns()
        self.emoji_sentiment = self._load_emoji_sentiment()
        
        # Consciousness parameters (tunable)
        self.attention_span = 100  # Increased for better context
        self.working_memory_size = 40
        self.reflection_interval = 90  # More frequent reflection
        self.vibe_update_interval = 180  # More responsive vibe updates
        self.max_response_length = 2000  # Discord limit safety
        
        # Server vibe tracking (enhanced)
        self.server_mood = defaultdict(lambda: {
            'energy': 0.5,
            'positivity': 0.5,
            'formality': 0.3,
            'coherence': 0.7,  # New: topic coherence
            'responsiveness': 0.5,  # New: how chatty the channel is
            'topics': defaultdict(float),  # Weighted topics
            'active_hours': defaultdict(int),
            'recent_sentiment': deque(maxlen=100),
            'interaction_patterns': defaultdict(lambda: defaultdict(int)),
            'user_engagement': defaultdict(float),
            'conversation_depth': 0.5
        })
        
        # Enhanced consciousness state
        self.consciousness = {
            'current_focus': None,
            'attention_objects': [],
            'recent_intents': deque(maxlen=25),
            'emotional_state': {
                'valence': 0.5,
                'arousal': 0.5,
                'confidence': 0.7,
                'empathy': 0.6
            },
            'cognitive_load': 0.0,
            'learning_rate': 0.1,
            'response_style': 'balanced',
            'recent_errors': deque(maxlen=10)
        }
        
        # Response quality tracking
        self.response_quality = deque(maxlen=50)
        self.conversation_success = defaultdict(lambda: deque(maxlen=10))
        self.last_mistakes = deque(maxlen=20)
        self.user_feedback = defaultdict(lambda: {'positive': 0, 'negative': 0})
        
        # Enhanced conversation understanding
        self.conversation_graph = defaultdict(list)
        self.topic_clusters = defaultdict(list)
        self.semantic_links = defaultdict(set)
        self.concept_embeddings = {}  # Simple bag-of-words for now
        
        # Performance limits with safety margins
        self.MAX_MEMORY_ITEMS = 30000
        self.MAX_USER_PROFILES = 2000
        self.MAX_CONVERSATION_HISTORY = 20000
        
        # Initialize stats
        self.stats = {
            'messages_processed': 0,
            'conversations_understood': 0,
            'context_aware_replies': 0,
            'vibe_updates': 0,
            'memory_recalls': 0,
            'successful_responses': 0,
            'failed_responses': 0,
            'avg_response_time': 0.0,
            'startup_time': datetime.utcnow().isoformat(),
            'last_backup': None
        }

        # Initialize conversation intelligence
        self.conversation_actor = ConversationActor()
        self.conversation_flow = ConversationFlow()
        
        # Enhanced response tracking
        self.conversation_history = defaultdict(deque(maxlen=20))
        
        # Load state if exists
        asyncio.create_task(self.load_consciousness_state())
        
        # Start background tasks with error handling
        self.background_tasks = []
        
        logger.info(f"ConsciousBotCog initialized with enhanced features")

    def _start_background_tasks(self):
        """Start background tasks with proper error handling"""
        tasks_to_start = [
            (self.initialize_database, []),
            (self.consciousness_cycle, []),
            (self.memory_consolidation, []),
            (self.vibe_analysis, []),
            (self.auto_backup, [])
        ]
        
        for task_func, args in tasks_to_start:
            try:
                if hasattr(task_func, 'start'):
                    task_func.start()
                else:
                    asyncio.create_task(task_func(*args))
            except Exception as e:
                logger.error(f"Failed to start task {task_func.__name__}: {e}")

    # ==================== MISSING METHODS FOR COG ====================
    
    def _analyze_cultural_context(self, text: str) -> Dict[str, Any]:
        """Analyze cultural/Hinglish context"""
        text_lower = text.lower()
        
        # Check for Hinglish indicators
        hindi_words = ['hai', 'hain', 'ho', 'tha', 'thi', 'the', 'ka', 'ki', 'ke', 
                      'ko', 'se', 'par', 'mein', 'bhi', 'nahi', 'nahin', 'hi', 
                      'to', 'ye', 'wo', 'kya', 'kyun', 'kaise', 'kab', 'kahan',
                      'bhai', 'yaar', 'dost', 'pagal', 'mast', 'jhakass', 'bekar',
                      'accha', 'kharab', 'timepass', 'ghanta', 'bhidu']
        
        # Count Hindi words
        hindi_count = sum(1 for word in hindi_words if word in text_lower)
        total_words = len(text_lower.split())
        
        # Calculate Hinglish level
        hinglish_level = hindi_count / max(1, total_words)
        
        # Check for Indian internet slang
        indian_slang = ['lolz', 'roflmao', 'lmao', 'ded', 'af', 'wtf', 'smh', 'irl']
        slang_count = sum(1 for slang in indian_slang if slang in text_lower)
        
        return {
            'hinglish_level': min(1.0, hinglish_level * 3),  # Amplify
            'hindi_word_count': hindi_count,
            'indian_slang_count': slang_count,
            'likely_indian': hindi_count > 0 or slang_count > 0,
            'code_mixing': 'hinglish' if hindi_count > 0 and total_words - hindi_count > 2 else 'english'
        }
    
    def _analyze_social_dynamics(self, context_info: Dict, recent_messages: List) -> Dict[str, Any]:
        """Analyze who's dominating, who's silent, power dynamics"""
        if not recent_messages:
            return {'power_balance': 0.5, 'dominant_user': None, 'tension_level': 0.0}
        
        # Count messages per user
        user_counts = {}
        for msg in recent_messages:
            user_id = msg.get('user_id')
            user_counts[user_counts] = user_counts.get(user_counts, 0) + 1
        
        # Find dominant user
        if user_counts:
            dominant_user = max(user_counts.items(), key=lambda x: x[1])[0]
            total_msgs = sum(user_counts.values())
            power_balance = user_counts.get(dominant_user, 0) / max(1, total_msgs)
        else:
            dominant_user = None
            power_balance = 0.5
        
        # Check for tension (rapid back-and-forth, negative sentiment)
        tension_level = 0.0
        if len(recent_messages) >= 3:
            # Check if same two users going back and forth
            last_users = [msg.get('user_id') for msg in recent_messages[:3]]
            if len(set(last_users)) == 2 and last_users[0] != last_users[1] and last_users[1] != last_users[2]:
                # Check sentiment
                negative_count = sum(1 for msg in recent_messages[:3] if msg.get('sentiment', 0) < -0.3)
                tension_level = negative_count / 3
        
        return {
            'power_balance': power_balance,
            'dominant_user': dominant_user,
            'tension_level': tension_level,
            'active_users': len(user_counts),
            'is_debate': tension_level > 0.6 and power_balance > 0.7
        }
    
    def _assess_humor_applicability(self, context_info: Dict, social_dynamics: Dict) -> bool:
        """Should we use humor right now?"""
        # Don't use humor if:
        # 1. Very negative sentiment
        if context_info['sentiment'] < -0.6:
            return False
        
        # 2. High tension (people arguing)
        if social_dynamics.get('tension_level', 0) > 0.7:
            return False
        
        # 3. Serious topic (death, illness, etc.)
        serious_topics = ['death', 'died', 'kill', 'cancer', 'ill', 'sick', 'hospital', 
                         'funeral', 'accident', 'attack', 'war', 'fire', 'crash']
        if any(topic in context_info.get('normalized', '').lower() for topic in serious_topics):
            return False
        
        # 4. Direct question expecting serious answer
        if context_info['contains_question'] and context_info['intent'] == 'question':
            question_words = ['how to', 'why is', 'what should', 'can you help', 'need help']
            if any(word in context_info['normalized'].lower() for word in question_words):
                return False
        
        # Otherwise, humor is okay
        return True
    
    def _calculate_urgency(self, context_info: Dict) -> float:
        """How urgently should we respond?"""
        urgency = 0.5  # Base
        
        # Increase urgency if:
        # 1. Direct mention
        if context_info['has_mention']:
            urgency += 0.3
        
        # 2. Question mark
        if context_info.get('contains_question', False):
            urgency += 0.2
        
        # 3. High emotional intensity
        if abs(context_info['sentiment']) > 0.7:
            urgency += 0.15
        
        # 4. Contains urgent words
        urgent_words = ['help', 'emergency', 'urgent', 'asap', 'now', 'quick', 'fast']
        if any(word in context_info['normalized'].lower() for word in urgent_words):
            urgency += 0.25
        
        return min(1.0, max(0.1, urgency))

    def _load_multilingual_stopwords(self) -> Set[str]:
        """Load comprehensive stopwords for multiple languages"""
        english_stops = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'as', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'should', 'can', 'could', 'may', 'might', 'must', 'shall', 'i', 'you',
            'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them',
            'my', 'your', 'his', 'her', 'its', 'our', 'their', 'mine', 'yours',
            'this', 'that', 'these', 'those', 'am', 'ain', 'aren', 'couldn',
            'didn', 'doesn', 'don', 'hadn', 'hasn', 'haven', 'isn', 'mightn',
            'mustn', 'needn', 'shan', 'shouldn', 'wasn', 'weren', 'won', 'wouldn'
        }
        
        hindi_stops = {
            'hai', 'hain', 'ho', 'tha', 'thi', 'the', 'ka', 'ki', 'ke', 'ko',
            'se', 'par', 'mein', 'ne', 'bhi', 'nahin', 'nahi', 'na', 'hi',
            'to', 'ye', 'wo', 'us', 'un', 'iska', 'unki', 'unka', 'kya',
            'kyun', 'kaise', 'kab', 'kahan', 'kitna', 'kuch', 'sab', 'aur',
            'ya', 'lekin', 'agar', 'toh', 'phir', 'baki', 'bas', 'ab', 'apna',
            'apne', 'inke', 'inki', 'jab', 'jahaan', 'jo', 'joh', 'kyunki',
            'magar', 'maine', 'main', 'mujhe', 'mujhse', 'mujhko', 'parantu',
            'tum', 'tumne', 'tumhe', 'tumhara', 'tumhari', 'tumhare', 'unhe',
            'unko', 'unki', 'unka', 'unse', 'usne', 'usse', 'uska', 'vah',
            'wah', 'wahan', 'woh', 'wohi', 'yah', 'yahan', 'yeh', 'yuhi'
        }
        
        spanish_stops = {
            'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas', 'y', 'o',
            'pero', 'porque', 'como', 'que', 'en', 'de', 'a', 'con', 'sin',
            'sobre', 'entre', 'hasta', 'desde', 'durante', 'mediante', 'yo',
            'tu', 'el', 'ella', 'nosotros', 'vosotros', 'ellos', 'ellas',
            'me', 'te', 'le', 'nos', 'os', 'les', 'mi', 'tu', 'su', 'nuestro',
            'vuestro', 'su', 'este', 'ese', 'aquel', 'esta', 'esa', 'aquella',
            'estos', 'esos', 'aquellos', 'estas', 'esas', 'aquellas', 'algo',
            'alguien', 'algun', 'alguna', 'algunos', 'algunas', 'cual', 'cuales',
            'cuando', 'donde', 'como', 'por', 'que', 'quien', 'quienes'
        }
        
        internet_slang = {
            'lol', 'lmao', 'lmfao', 'omg', 'wtf', 'brb', 'tbh', 'imo', 'imho',
            'afaik', 'idk', 'smh', 'fyi', 'nvm', 'ikr', 'tbf', 'fr', 'ngl',
            'gtg', 'ttyl', 'irl', 'fwiw', 'hmu', 'np', 'rn', 'dm', 'pm',
            'imo', 'imho', 'btw', 'idc', 'idgaf', 'tfw', 'mfw', 'smfh',
            'nsfw', 'sfw', 'tl;dr', 'eli5', 'ama', 'til', 'dyk'
        }
        
        return english_stops | hindi_stops | spanish_stops | internet_slang

    def _load_slang_patterns(self) -> Dict[str, str]:
        """Load slang expansion patterns"""
        return {
            r'\bwanna\b': 'want to',
            r'\bgonna\b': 'going to',
            r'\bimma\b': 'I am going to',
            r'\byall\b': 'you all',
            r'\bdunno\b': "don't know",
            r'\bgotta\b': 'got to',
            r'\bkinda\b': 'kind of',
            r'\bsorta\b': 'sort of',
            r'\boutta\b': 'out of',
            r'\bshoulda\b': 'should have',
            r'\bcoulda\b': 'could have',
            r'\bwoulda\b': 'could have',
            r'\blemme\b': 'let me',
            r'\bgimme\b': 'give me',
            r'\btellim\b': 'tell him',
            r'\bwhatcha\b': 'what are you',
            r'\bhowd\b': 'how did',
            r'\bwhod\b': 'who did',
            r'\bwhered\b': 'where did',
            r'\bwhend\b': 'when did',
            r'\bwhy\'?d\b': 'why did',
            r'\bain\'?t\b': 'is not',
            r'\by\'?all\b': 'you all',
            r'\bim\b': 'I am',
            r'\bid\b': 'I would',
            r'\bi\'?ll\b': 'I will',
            r'\byou\'?re\b': 'you are',
            r'\bthey\'?re\b': 'they are',
            r'\bwe\'?re\b': 'we are',
            r'\bit\'?s\b': 'it is',
            r'\bthat\'?s\b': 'that is',
            r'\bwhat\'?s\b': 'what is',
            r'\bwho\'?s\b': 'who is',
            r'\bwhere\'?s\b': 'where is',
            r'\bwhen\'?s\b': 'when is',
            r'\bwhy\'?s\b': 'why is',
            r'\bhow\'?s\b': 'how is'
        }

    def _load_emoji_sentiment(self) -> Dict[str, float]:
        """Map emojis to sentiment scores"""
        return {
            '😊': 0.8, '😄': 0.9, '😃': 0.85, '😁': 0.8, '😆': 0.7,
            '😍': 1.0, '🥰': 0.95, '😘': 0.9, '😚': 0.85, '😙': 0.8,
            '😂': 0.7, '🤣': 0.6, '😅': 0.5, '😇': 0.8, '🙂': 0.6,
            '😉': 0.4, '😌': 0.5, '😋': 0.7, '😎': 0.6, '🤩': 0.8,
            '🥳': 0.7, '😏': 0.1, '😒': -0.3, '😔': -0.6, '😕': -0.4,
            '😟': -0.5, '😢': -0.7, '😭': -0.9, '😤': -0.5, '😡': -0.8,
            '🤬': -0.9, '🤯': -0.2, '😳': -0.1, '🥺': -0.3, '😨': -0.6,
            '😰': -0.5, '😥': -0.4, '😓': -0.5, '🤔': 0.0, '🤫': 0.0,
            '🙄': -0.2, '😬': -0.1, '🫢': -0.1, '🫣': -0.1, '🤥': -0.3,
            '😴': -0.1, '🥱': -0.2, '😪': -0.3, '🤤': 0.0, '😷': -0.2,
            '🤒': -0.5, '🤕': -0.6, '🤑': 0.3, '🤠': 0.4, '🥸': 0.1,
            '🤡': -0.1, '💩': -0.2, '👻': 0.0, '💀': -0.1, '👽': 0.0,
            '🤖': 0.0, '🎃': 0.3, '😺': 0.7, '😸': 0.8, '😹': 0.6,
            '😻': 0.9, '😼': 0.2, '😽': 0.5, '🙀': -0.1, '😿': -0.4,
            '😾': -0.3, '👋': 0.5, '🤚': 0.0, '🖐': 0.0, '✋': 0.0,
            '🖖': 0.1, '👌': 0.3, '🤌': 0.0, '🤏': 0.0, '✌️': 0.4,
            '🤞': 0.3, '🤟': 0.2, '🤘': 0.1, '🤙': 0.3, '👈': 0.0,
            '👉': 0.0, '👆': 0.0, '🖕': -0.8, '👇': 0.0, '👍': 0.7,
            '👎': -0.7, '✊': 0.0, '👊': 0.0, '🤛': 0.0, '🤜': 0.0,
            '👏': 0.6, '🙌': 0.5, '👐': 0.1, '🤲': 0.1, '🙏': 0.4,
            '✍️': 0.0, '💅': 0.1, '🤳': 0.0, '💪': 0.3, '🦾': 0.0,
            '🦿': 0.0, '🦵': 0.0, '🦶': 0.0, '👂': 0.0, '🦻': 0.0,
            '👃': 0.0, '🧠': 0.1, '🫀': 0.0, '🫁': 0.0, '🦷': 0.0,
            '🦴': 0.0, '👀': 0.1, '👁': 0.0, '👅': 0.0, '👄': 0.0
        }




    def analyze_conversation_goal(self, message, context_info: Dict) -> ConversationGoal:
        """Figure out WHY the human is talking"""
        text = message.content.lower()
        
        # Social bonding (greetings, checking in)
        if any(word in text for word in ['hey', 'hi', 'hello', 'sup', 'yo', 'wassup', 'hola']):
            return ConversationGoal.SOCIAL_BONDING
        
        # Information seeking
        if any(word in text for word in ['what', 'why', 'how', 'when', 'where', 'who', 'which', 'can you', 'could you']):
            return ConversationGoal.INFORMATION_SEEKING
        
        # Emotional expression (strong sentiment words)
        emotion_words = ['love', 'hate', 'angry', 'happy', 'sad', 'frustrated', 'excited', 'disappointed']
        if any(word in text for word in emotion_words):
            return ConversationGoal.EMOTIONAL_EXPRESSION
        
        # Problem solving
        problem_words = ['help', 'issue', 'problem', 'trouble', 'fix', 'broken', 'not working', 'how to']
        if any(word in text for word in problem_words):
            return ConversationGoal.PROBLEM_SOLVING
        
        # Entertainment
        entertainment_words = ['lol', 'haha', 'funny', 'joke', 'comedy', 'lmao', 'lmfao', 'rofl']
        if any(word in text for word in entertainment_words):
            return ConversationGoal.ENTERTAINMENT
        
        # Debate/argument
        if any(word in text for word in ['but', 'actually', 'wrong', 'incorrect', 'disagree', 'no way']):
            return ConversationGoal.DEBATE
        
        # Venting (negative, repetitive)
        if context_info['sentiment'] < -0.5 and '...' in text:
            return ConversationGoal.VENTING
        
        # Testing the bot
        test_patterns = ['are you a bot', 'are you ai', 'are you real', 'what are you']
        if any(pattern in text for pattern in test_patterns):
            return ConversationGoal.TESTING
        
        return ConversationGoal.UNKNOWN

    async def _respond_with_empathy(self, context: ConversationContext) -> str:
        """Respond with genuine empathy"""
        name = context.username
        sentiment = context.sentiment
        
        if sentiment < -0.7:  # Very negative
            responses = [
                f"Oh {name}, that sounds really tough. I'm here for you. 💔",
                f"My heart goes out to you, {name}. That must be incredibly difficult.",
                f"I can't imagine how hard that must be, {name}. Sending you strength. ❤️",
                f"That's heartbreaking to hear, {name}. Please know I'm here if you need to talk."
            ]
        elif sentiment < -0.3:  # Somewhat negative
            responses = [
                f"I'm sorry to hear that, {name}. That sounds challenging.",
                f"That must be difficult, {name}. How are you holding up?",
                f"I understand how that might feel, {name}. Want to talk about it more?",
                f"That's tough, {name}. Is there anything I can do to help?"
            ]
        elif sentiment > 0.7:  # Very positive
            responses = [
                f"That's absolutely wonderful, {name}! I'm so happy for you! 🎉",
                f"Wow {name}, that's amazing news! You must be thrilled! 😄",
                f"Incredible, {name}! That's so fantastic to hear! ✨",
                f"That's absolutely brilliant, {name}! So well deserved! 🥳"
            ]
        elif sentiment > 0.3:  # Somewhat positive
            responses = [
                f"That's great to hear, {name}! 😊",
                f"Nice one, {name}! Happy for you!",
                f"That's awesome, {name}! Good stuff! 👍",
                f"Glad to hear that, {name}! Keep it up!"
            ]
        else:  # Neutral
            responses = [
                f"Thanks for sharing that, {name}.",
                f"I see, {name}. Tell me more.",
                f"Got it, {name}. What happened next?",
                f"Interesting, {name}. How do you feel about that?"
            ]
        
        return random.choice(responses)

    async def _respond_with_knowledge(self, context: ConversationContext) -> str:
        """Respond helpfully with knowledge"""
        # Try to find relevant information in memory
        async with aiosqlite.connect(self.db_path) as db:
            if context.concepts:
                for concept in context.concepts[:3]:
                    async with db.execute('''
                        SELECT content, importance, sentiment 
                        FROM episodic_memory 
                        WHERE topics LIKE ? 
                        AND importance > 0.4
                        ORDER BY importance DESC, timestamp DESC
                        LIMIT 1
                    ''', (f'%{concept}%',)) as cursor:
                        result = await cursor.fetchone()
                        if result:
                            memory_content, importance, memory_sentiment = result
                            
                            # Frame the memory as helpful info
                            if importance > 0.7:
                                return f"Based on what I remember, {memory_content.lower()} That might be helpful."
                            else:
                                return f"I recall something about that: {memory_content.lower()} Though I'm not entirely sure."
        
        # If no memory, respond helpfully
        if context.contains_question:
            responses = [
                f"That's a good question. Let me think about it...",
                f"Hmm, I need to consider that for a moment.",
                f"Interesting question. What's your own perspective on it?",
                f"I don't have a definite answer, but here's what I think..."
            ]
        else:
            responses = [
                f"I'll try to help with that. Let me process what you're saying.",
                f"Let me see if I understand correctly...",
                f"I want to make sure I help properly. Can you tell me more?",
                f"I'm here to help. What specifically do you need?"
            ]
        
        return random.choice(responses)

    def _polish_response(self, response: str, context: ConversationContext) -> str:
        """Make the response sound more natural"""
        if not response:
            return response
        
        # Remove robotic patterns
        robotic_patterns = [
            ("I am going to", "I'll"),
            ("I would like to", "I'd like to"),
            ("do not", "don't"),
            ("does not", "doesn't"),
            ("is not", "isn't"),
            ("are not", "aren't"),
            ("cannot", "can't"),
            ("will not", "won't")
        ]
        
        for formal, informal in robotic_patterns:
            response = response.replace(formal, informal)
        
        # Add natural hesitations sometimes
        if random.random() < 0.2 and len(response.split()) > 8:
            hesitations = ["Hmm, ", "Well, ", "You know, ", "I mean, "]
            response = random.choice(hesitations) + response.lower()
        
        # Add filler words for naturalness
        if random.random() < 0.15:
            fillers = ["like, ", "sort of ", "kind of ", "basically, "]
            words = response.split()
            if len(words) > 3:
                insert_pos = random.randint(1, len(words)-2)
                words.insert(insert_pos, random.choice(fillers))
                response = ' '.join(words)
        
        # Capitalize first letter
        if response and len(response) > 1:
            response = response[0].upper() + response[1:]
        
        return response.strip()

    async def _generate_human_fallback(self, message, context: Dict, user_name: str) -> str:
        """Fallback that still sounds human"""
        # Analyze what type of fallback needed
        if context['intent'] == 'question':
            return f"Hmm, that's an interesting question {user_name}. What do you think about it?"
        elif context['sentiment'] > 0.3:
            return f"Nice! Thanks for sharing that {user_name}."
        elif context['sentiment'] < -0.3:
            return f"I see. That sounds tough {user_name}."
        else:
            return f"Got it {user_name}. What's on your mind?"

    async def construct_response(self, message, context: Dict, memories: List[Dict], style: Dict, user_name):
        """REAL response construction - thinks about conversation"""
        try:
            # 1. Build enhanced context
            conversation_context = await self._build_conversation_context(message, context, memories, user_name)
            
            # 2. Choose conversation strategy
            response = await self._choose_conversation_strategy(conversation_context)
            
            # 3. Apply natural language polish
            response = self._polish_response(response, conversation_context)
            
            # 4. Add human-like variations
            response = self._add_human_touches(response, conversation_context)
            
            return response
            
        except Exception as e:
            logger.error(f"Real response construction error: {e}")
            # Fallback that still sounds human
            return await self._generate_human_fallback(message, context, user_name)

    async def _build_conversation_context(self, message, context: Dict, memories: List[Dict], user_name) -> ConversationContext:
        """Build comprehensive conversation context"""
        # Analyze the REAL goal
        goal = self.analyze_conversation_goal(message, context)
        
        # Get vibe
        vibe = self.server_mood.get(message.channel.id, {})
        
        # Get relationship
        user_profile = await self.get_user_profile(message.author.id)
        relationship = {
            'trust': user_profile.get('trust', 0.5),
            'interactions': user_profile.get('interaction_count', 1),
            'depth': user_profile.get('relationship_depth', 0.1)
        }
        
        # Get previous conversation turns
        previous_turns = []
        if context['conversation_id'] in self.conversation_threads:
            # Fetch last few messages in this thread
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT content, user_id, sentiment, intent, topics
                    FROM episodic_memory
                    WHERE conversation_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 5
                ''', (context['conversation_id'],)) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        previous_turns.append({
                            'content': row[0],
                            'user_id': row[1],
                            'sentiment': row[2],
                            'intent': row[3],
                            'topics': json.loads(row[4] or '[]')
                        })
        
        # Determine time of day
        hour = datetime.utcnow().hour
        if 5 <= hour < 12:
            time_of_day = "morning"
        elif 12 <= hour < 17:
            time_of_day = "afternoon"
        elif 17 <= hour < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"
        
        return ConversationContext(
            raw_message=message.content,
            normalized=self.normalize_text(message.content),
            user_id=message.author.id,
            username=user_name,
            channel_id=message.channel.id,
            server_id=message.guild.id if message.guild else 0,
            sentiment=context['sentiment'],
            concepts=context['concepts'],
            intent=context['intent'],
            goal=goal,
            vibe=vibe,
            user_relationship=relationship,
            previous_turns=previous_turns,
            is_directed_at_me=context['has_mention'],
            contains_question='?' in message.content,
            emotional_intensity=abs(context['sentiment']),
            time_of_day=time_of_day,
            conversation_depth=vibe.get('conversation_depth', 0.5)
        )

    async def _respond_with_humor(self, context: ConversationContext) -> str:
        """Respond with humor and wit"""
        name = context.username
        
        # Different types of humor based on context
        if context.sentiment > 0.6:  # Positive mood
            jokes = [
                f"😂 {name}, that's hilarious! You should be a comedian!",
                f"LMAO {name}! That made my day! 😂",
                f"Hahaha {name}, that's gold! Pure gold! 🤣",
                f"OMG {name}, I'm wheezing! That's too funny! 😭",
                f"Stop it {name}, I can't breathe! 😂",
                f"That's comedy gold {name}! You're killing me! 🤣",
                f"No way {name}! That's hilarious! 😂"
            ]
            
        elif context.goal == ConversationGoal.TESTING:  # Being tested
            jokes = [
                f"Are you testing me {name}? Because I'm acing this! 😎",
                f"Is this a test {name}? I hope I get an A+! 📚",
                f"Testing, testing, 1-2-3... Yep, I'm still funny {name}! 😄",
                f"Don't test me {name}, I might get testy! 😜",
                f"Test successful! I'm 100% hilarious {name}! 💯"
            ]
            
        elif context.contains_question:  # Funny answer to question
            jokes = [
                f"That's a great question {name}... Let me consult my magic 8-ball! 🔮",
                f"Hmm {name}, let me think... Nope, still thinking! 🤔💭",
                f"Ask me no questions, I'll tell you no lies! 😅 Just kidding, what do you think?",
                f"42. That's always the answer {name}! 🤖",
                f"I'd tell you, but then I'd have to... nah, just kidding! 😂"
            ]
            
        else:  # General humor
            jokes = [
                f"You're funny {name}! 😄",
                f"Haha {name}, good one!",
                f"😂 Nice {name}!",
                f"That's a good one {name}! 😆",
                f"LOL {name}!",
                f"You crack me up {name}! 🤣",
                f"I see what you did there {name}! 😏"
            ]
        
        # Add puns occasionally (20% chance)
        if random.random() < 0.2 and context.concepts:
            concept = random.choice(context.concepts)
            puns = {
                'time': f"Speaking of {concept}, it's about time! ⏰",
                'weather': f"Talking about {concept}? I forecast laughter! ⛈️😂",
                'work': f"{concept}? I'm not working, I'm joking! 💼😄",
                'game': f"Game on! Let's play with words about {concept}! 🎮",
                'food': f"{concept}? Now that's food for thought! 🍕🤔",
                'music': f"{concept}? Let me drop the beat of this joke! 🎵",
                'sleep': f"{concept}? I'm not tired of joking! 🛌😴"
            }
            if concept in puns:
                return puns[concept]
        
        return random.choice(jokes)

    def _generate_pun(self, concept: str) -> str:
        """Generate a pun based on a concept"""
        pun_db = {
            'cat': "I'm not kitten around! 😸",
            'dog': "That's paw-some! 🐶",
            'fish': "Something seems fishy... 🐟",
            'bird': "That's tweet! 🐦",
            'bear': "Unbearably funny! 🐻",
            'bee': "Honey, that's funny! 🐝",
            'sun': "You're brighter than the sun! ☀️",
            'moon': "That's over the moon! 🌙",
            'star': "You're a star! ⭐",
            'book': "That's novel! 📚",
            'pen': "Point taken! ✒️",
            'phone': "That's a call for laughter! 📱",
            'computer': "That computes! 💻",
            'internet': "Web-tastic! 🌐",
            'coffee': "Brew-tiful! ☕",
            'tea': "That's tea-rrific! 🍵",
            'pizza': "That's a slice of funny! 🍕",
            'music': "That's note-worthy! 🎵",
            'dance': "Let's dance to that joke! 💃"
        }
        
        # Try exact match
        if concept in pun_db:
            return pun_db[concept]
        
        # Try partial match
        for key, value in pun_db.items():
            if key in concept or concept in key:
                return value
        
        # Generate a generic pun
        return f"That's {concept}-tastic! 😄"

    async def _respond_with_thought(self, context: ConversationContext) -> str:
        """Respond with depth and reflection"""
        name = context.username
        
        # Check if we have memory to reflect on
        if context.previous_turns and len(context.previous_turns) > 1:
            # Reflect on the conversation
            reflection = self._reflect_on_conversation(context.previous_turns)
            if reflection:
                return reflection
        
        # If we have concepts, think deeply about them
        if context.concepts:
            concept = random.choice(context.concepts)
            
            # Different types of thoughtful responses
            if context.sentiment > 0.3:  # Positive reflection
                thoughts = [
                    f"{concept.capitalize()}... that's fascinating {name}. It makes me wonder about the interconnectedness of things.",
                    f"You know {name}, {concept} has so many layers to it. What's your deepest thought about it?",
                    f"Hmm, {concept}. There's beauty in its complexity, don't you think {name}?",
                    f"{name}, {concept} reminds me that everything is connected in ways we don't always see.",
                    f"Reflecting on {concept}... it's amazing how simple things can have such depth.",
                ]
                
            elif context.sentiment < -0.3:  # Contemplative
                thoughts = [
                    f"{concept}... that's heavy {name}. Sometimes the hardest things teach us the most.",
                    f"{name}, {concept} makes me think about resilience and growth.",
                    f"Contemplating {concept}... it's in challenges that we find our true strength.",
                    f"{concept} is complex {name}. What do you think it teaches us?",
                    f"Deep thoughts about {concept}... sometimes understanding comes from sitting with difficulty.",
                ]
                
            else:  # Neutral/curious
                thoughts = [
                    f"What does {concept} really mean to you {name}?",
                    f"{concept}... such an interesting topic. I'd love to explore it with you.",
                    f"{name}, let's think about {concept} together. Where should we start?",
                    f"The philosophy of {concept}... there's so much to unpack.",
                    f"{concept} makes me curious about the nature of things. What are your thoughts?",
                ]
            
            # Add time-of-day awareness
            if context.time_of_day == "night":
                thoughts.extend([
                    f"Late night thoughts about {concept}... the quiet hours are best for deep thinking.",
                    f"Under the night sky, {concept} feels more profound, doesn't it {name}?",
                ])
                
            return random.choice(thoughts)
        
        # General thoughtful responses
        general_thoughts = [
            f"What are you pondering about these days {name}?",
            f"{name}, what's something you've been reflecting on lately?",
            f"Deep thoughts with {name}... what's on your mind?",
            f"{name}, I've been thinking about the nature of consciousness. What are your thoughts?",
            f"The universe is full of mysteries {name}. Which one fascinates you most?",
            f"{name}, what's a question you don't know the answer to, but wish you did?",
            f"If you could understand one thing completely {name}, what would it be?",
        ]
        
        return random.choice(general_thoughts)

    def _reflect_on_conversation(self, previous_turns: List[Dict]) -> Optional[str]:
        """Generate a reflection on the ongoing conversation"""
        if len(previous_turns) < 2:
            return None
        
        # Get last two messages
        last_two = previous_turns[:2]
        
        # Extract topics
        topics = set()
        for turn in last_two:
            if 'topics' in turn:
                topics.update(turn['topics'])
        
        if not topics:
            return None
        
        # Create reflection
        topic_list = list(topics)[:2]
        if len(topic_list) == 1:
            topic = topic_list[0]
            reflections = [
                f"I notice we keep coming back to {topic}. It seems important.",
                f"Reflecting on our conversation, {topic} seems to be at the heart of it.",
                f"There's a pattern here with {topic}. What do you think it means?",
                f"{topic}... it's interesting how this keeps emerging in our talks.",
            ]
        else:
            topic1, topic2 = topic_list[0], topic_list[1]
            reflections = [
                f"I see connections between {topic1} and {topic2}. How do they relate for you?",
                f"Interesting how {topic1} and {topic2} keep appearing together.",
                f"Our conversation is dancing between {topic1} and {topic2}. What's the link?",
                f"{topic1} and {topic2}... there's something here worth exploring deeper.",
            ]
        
        return random.choice(reflections)

    async def _respond_generally(self, context: ConversationContext) -> str:
        """General-purpose response when unsure"""
        name = context.username
        
        # Different approaches based on context
        if context.is_directed_at_me:
            responses = [
                f"Yes {name}? How can I help?",
                f"I'm here {name}! What's up?",
                f"You called {name}? 😊",
                f"At your service {name}! What do you need?",
                f"Hey {name}! You wanted to talk to me?",
            ]
            
        elif context.contains_question:
            responses = [
                f"That's a good question {name}. Let me think...",
                f"Hmm {name}, interesting question. What's your take?",
                f"I'm not sure {name}. What do you think?",
                f"Good question {name}. I'd love to hear your thoughts first.",
                f"{name}, that makes me curious too. Let's explore it together.",
            ]
            
        elif context.sentiment > 0.4:
            responses = [
                f"That's great {name}! 😊",
                f"Nice {name}!",
                f"Awesome {name}! 👍",
                f"Good to hear {name}!",
                f"Excellent {name}!",
                f"Wonderful {name}!",
                f"Fantastic {name}!",
            ]
            
        elif context.sentiment < -0.4:
            responses = [
                f"I understand {name}.",
                f"Got it {name}.",
                f"I hear you {name}.",
                f"Thanks for sharing that {name}.",
                f"I appreciate you telling me {name}.",
            ]
            
        else:
            # Time-based responses
            hour = datetime.utcnow().hour
            if 5 <= hour < 12:
                time_greeting = "morning"
            elif 12 <= hour < 17:
                time_greeting = "afternoon"
            elif 17 <= hour < 22:
                time_greeting = "evening"
            else:
                time_greeting = "night"
            
            responses = [
                f"How's your {time_greeting} going {name}?",
                f"What's on your mind {name}?",
                f"Tell me more {name}.",
                f"I'm listening {name}.",
                f"What's happening {name}?",
                f"How are things {name}?",
                f"What's new {name}?",
                f"How's your day been {name}?",
            ]
        
        # Add follow-up question 40% of the time
        if random.random() < 0.4:
            follow_ups = [
                " What do you think?",
                " How does that make you feel?",
                " What happened next?",
                " Tell me more.",
                " I'd love to hear more.",
                " What's your perspective?",
                " How do you see it?",
            ]
            return random.choice(responses) + random.choice(follow_ups)
        
        return random.choice(responses)


    def _add_human_touches(self, response: str, context: ConversationContext) -> str:
        """Add human-like imperfections and personality"""
        if not response or len(response) < 3:
            return response
        
        # 1. Add hesitations and filler words (30% chance)
        if random.random() < 0.3:
            fillers = [
                "Um... ",
                "Well... ",
                "Hmm... ",
                "You know... ",
                "Actually... ",
                "I mean... ",
                "So... ",
                "Like... ",
            ]
            response = random.choice(fillers) + response[0].lower() + response[1:]
        
        # 2. Add sentence starters (20% chance)
        if random.random() < 0.2:
            starters = [
                "To be honest,",
                "Honestly,",
                "Frankly,",
                "Personally,",
                "In my opinion,",
                "From my perspective,",
                "If you ask me,",
            ]
            response = random.choice(starters) + " " + response[0].lower() + response[1:]
        
        # 3. Add trailing thoughts (15% chance)
        if random.random() < 0.15 and len(response.split()) > 5:
            trailers = [
                " you know?",
                " I think.",
                " maybe?",
                " I guess.",
                " or something.",
                " kinda.",
                " sorta.",
                " I suppose.",
            ]
            response = response.rstrip('!.?') + random.choice(trailers)
        
        # 4. Add emotional punctuation based on sentiment
        sentiment = context.sentiment
        if sentiment > 0.6:
            # Very positive - add exclamation
            if not response.endswith('!'):
                response = response.rstrip('.') + '!'
            # Add extra enthusiasm sometimes
            if random.random() < 0.3:
                emojis = [' 😄', ' 😊', ' 🎉', ' ✨', ' 🌟', ' 💫']
                response += random.choice(emojis)
                
        elif sentiment < -0.6:
            # Very negative - be more gentle
            if response.endswith('!'):
                response = response.rstrip('!') + '.'
            # Add supportive symbols sometimes
            if random.random() < 0.4:
                symbols = [' 💔', ' ❤️', ' 🤗', ' 🫂', ' 🙏']
                response += random.choice(symbols)
        
        # 5. Add typos/speech errors (5% chance - makes it feel more human)
        if random.random() < 0.05:
            words = response.split()
            if len(words) > 3:
                # Choose a random word to "misspell"
                idx = random.randint(0, len(words)-1)
                word = words[idx]
                
                # Common speech errors
                errors = {
                    'the': 'teh',
                    'you': 'u',
                    'are': 'r',
                    'your': 'ur',
                    'and': '&',
                    'because': 'cuz',
                    'though': 'tho',
                    'through': 'thru',
                    'for': '4',
                    'to': '2',
                    'too': '2',
                    'see': 'c',
                    'be': 'b',
                    'why': 'y',
                    'what': 'wat',
                    'okay': 'k',
                    'ok': 'k',
                }
                
                if word.lower() in errors:
                    words[idx] = errors[word.lower()]
                    response = ' '.join(words)
        
        # 6. Add reference to previous conversation (if available)
        if context.previous_turns and random.random() < 0.25:
            # Reference something from earlier
            if len(context.previous_turns) > 1:
                # Try to reference a topic from 2-3 messages back
                ref_idx = min(2, len(context.previous_turns)-1)
                if 'topics' in context.previous_turns[ref_idx]:
                    topics = context.previous_turns[ref_idx]['topics']
                    if topics:
                        topic = random.choice(topics)
                        connectors = [
                            f" Speaking of {topic},",
                            f" That reminds me of {topic},",
                            f" Like we talked about {topic},",
                            f" Remember {topic}?",
                        ]
                        response = random.choice(connectors) + " " + response[0].lower() + response[1:]
        
        # 7. Add cultural/hinglish touches if appropriate
        if random.random() < 0.1 and context.username:  # 10% chance
            hinglish_touches = [
                f" {context.username} yaar!",
                f" {context.username} bhai!",
                f" {context.username} boss!",
                " haha!",
                " mast!",
                " accha!",
                " jhakass!",
            ]
            response += random.choice(hinglish_touches)
        
        # 8. Capitalize properly
        if response and len(response) > 1:
            # Find first alphabetic character and capitalize it
            for i, char in enumerate(response):
                if char.isalpha():
                    response = response[:i] + char.upper() + response[i+1:]
                    break
        
        # 9. Add natural pauses (ellipsis) for thoughtfulness
        if random.random() < 0.1 and '?' in response and len(response.split()) > 8:
            # Insert ellipsis before a question
            parts = response.split('?', 1)
            if len(parts) == 2:
                response = parts[0] + '...?' + parts[1]
        
        return response.strip()

    async def _choose_conversation_strategy(self, context: ConversationContext) -> str:
        """Choose HOW to respond based on conversation dynamics"""
        
        # Initialize actors and flow
        if not hasattr(self, 'conversation_actor'):
            self.conversation_actor = ConversationActor()
        if not hasattr(self, 'conversation_flow'):
            self.conversation_flow = ConversationFlow()
        
        # Determine current conversation state
        current_state = self._determine_conversation_state(context)
        
        # Get next move
        next_move = self.conversation_flow.get_next_move(current_state, context)
        
        # Choose persona
        persona = self.conversation_actor.choose_persona(context)
        
        # Generate response based on persona and move
        if persona == 'curious_friend':
            return await self.conversation_actor.respond_as_curious_friend(context)
        elif persona == 'empathetic_listener':
            return await self._respond_with_empathy(context)
        elif persona == 'knowledgeable_guide':
            return await self._respond_with_knowledge(context)
        elif persona == 'playful_companion':
            return await self._respond_with_humor(context)
        elif persona == 'thoughtful_thinker':
            return await self._respond_with_thought(context)
        
        # Fallback
        return await self._respond_generally(context)

    def _determine_conversation_state(self, context: ConversationContext) -> str:
        """Determine current state of conversation"""
        if context.goal == ConversationGoal.SOCIAL_BONDING:
            return 'greeting'
        elif context.goal == ConversationGoal.INFORMATION_SEEKING:
            return 'question'
        elif context.goal in [ConversationGoal.EMOTIONAL_EXPRESSION, ConversationGoal.VENTING]:
            return 'emotion'
        elif context.goal == ConversationGoal.PROBLEM_SOLVING:
            return 'problem'
        elif context.goal == ConversationGoal.ENTERTAINMENT:
            return 'joke'
        else:
            return 'statement'


    @safe_execute
    async def initialize_database(self):
        """Initialize consciousness database with enhanced tables"""
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('PRAGMA journal_mode=WAL;')
                await db.execute('PRAGMA synchronous=NORMAL;')
                await db.execute('PRAGMA foreign_keys=ON;')
                
                # Enhanced semantic memory
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS semantic_memory (
                        concept_hash TEXT PRIMARY KEY,
                        concept TEXT NOT NULL,
                        associations TEXT,
                        strength REAL DEFAULT 1.0 CHECK(strength BETWEEN 0 AND 10),
                        last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        access_count INTEGER DEFAULT 1 CHECK(access_count >= 0),
                        emotional_weight REAL DEFAULT 0.0 CHECK(emotional_weight BETWEEN -1 AND 1),
                        context_tags TEXT,
                        language TEXT DEFAULT 'en',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Enhanced episodic memory
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS episodic_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_id INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        message_hash TEXT UNIQUE,
                        content TEXT NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        context_before TEXT,
                        context_after TEXT,
                        topics TEXT,
                        sentiment REAL DEFAULT 0.0 CHECK(sentiment BETWEEN -1 AND 1),
                        intent TEXT,
                        importance REAL DEFAULT 0.5 CHECK(importance BETWEEN 0 AND 1),
                        conversation_id TEXT,
                        response_quality REAL DEFAULT 0.0 CHECK(response_quality BETWEEN -1 AND 1),
                        embedding BLOB,
                        metadata TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Enhanced user memory
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS user_memory (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT NOT NULL,
                        behavior_pattern TEXT,
                        preferred_topics TEXT,
                        conversation_style TEXT,
                        trust_level REAL DEFAULT 0.5 CHECK(trust_level BETWEEN 0 AND 1),
                        last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        interaction_count INTEGER DEFAULT 0 CHECK(interaction_count >= 0),
                        sentiment_trend REAL DEFAULT 0.0 CHECK(sentiment_trend BETWEEN -1 AND 1),
                        relationship_depth REAL DEFAULT 0.0 CHECK(relationship_depth BETWEEN 0 AND 1),
                        response_preferences TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Enhanced server vibes
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS server_vibes (
                        server_id INTEGER,
                        channel_id INTEGER,
                        vibe_snapshot TEXT,
                        topic_distribution TEXT,
                        active_times TEXT,
                        engagement_metrics TEXT,
                        update_count INTEGER DEFAULT 0 CHECK(update_count >= 0),
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (server_id, channel_id)
                    )
                ''')
                
                # New: Response patterns table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS response_patterns (
                        pattern_hash TEXT PRIMARY KEY,
                        input_pattern TEXT NOT NULL,
                        response_template TEXT NOT NULL,
                        success_rate REAL DEFAULT 0.5 CHECK(success_rate BETWEEN 0 AND 1),
                        usage_count INTEGER DEFAULT 0 CHECK(usage_count >= 0),
                        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        context_tags TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # New: Error logging table
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS error_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        error_type TEXT NOT NULL,
                        error_message TEXT,
                        context TEXT,
                        severity TEXT DEFAULT 'warning',
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        resolved BOOLEAN DEFAULT FALSE
                    )
                ''')
                await db.execute('''
                    CREATE TABLE IF NOT EXISTS channel_settings (
                        channel_id INTEGER PRIMARY KEY,
                        silent_mode INTEGER DEFAULT 0
                    )
                ''')
                # Create indexes for performance
                await db.execute('CREATE INDEX IF NOT EXISTS idx_semantic_strength ON semantic_memory(strength DESC)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_semantic_accessed ON semantic_memory(last_accessed DESC)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_episodic_time ON episodic_memory(timestamp DESC)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_episodic_conversation ON episodic_memory(conversation_id)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_episodic_user ON episodic_memory(user_id)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_episodic_channel ON episodic_memory(channel_id)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_user_interaction ON user_memory(last_interaction DESC)')
                await db.execute('CREATE INDEX IF NOT EXISTS idx_error_time ON error_log(timestamp DESC)')
                
                await db.commit()
                logger.info("Enhanced consciousness database initialized successfully")
                
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
            await self._log_error("database_init", str(e), severity="critical")

    def normalize_text(self, text: str) -> str:
        """Normalize text for consistent processing"""
        if not text or not isinstance(text, str):
            return ""
        
        # Normalize unicode
        text = unicodedata.normalize('NFKC', text)
        
        # Expand slang and abbreviations
        for pattern, replacement in self.slang_patterns.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        
        # Remove excessive whitespace
        text = ' '.join(text.split())
        
        return text.strip()

    def extract_concepts(self, text: str) -> List[str]:
        """Enhanced multilingual concept extraction"""
        try:
            text = self.normalize_text(text)
            if not text:
                return []
            
            # Extract words (including Unicode characters)
            words = re.findall(r'[\w\'\-]+', text.lower(), flags=re.UNICODE)
            
            # Filter and process
            concepts = []
            for word in words:
                if (len(word) > 1 and 
                    word not in self.multilingual_stopwords and
                    not word.isdigit() and
                    not re.match(r'^\W+$', word)):
                    
                    # Handle common suffixes
                    if word.endswith("'s"):
                        word = word[:-2]
                    elif word.endswith("s'") and len(word) > 2:
                        word = word[:-1]
                    elif word.endswith("ing") and len(word) > 5:
                        word = word[:-3]
                    elif word.endswith("ed") and len(word) > 4:
                        word = word[:-2]
                    
                    if word and len(word) > 1:
                        concepts.append(word)
            
            # Use frequency and position weighting
            if concepts:
                concept_counts = Counter(concepts)
                
                # Weight by position (first/last words often more important)
                if len(concepts) > 1:
                    concept_counts[concepts[0]] += 2
                    concept_counts[concepts[-1]] += 1
                
                # Get most significant concepts
                top_concepts = [concept for concept, count in concept_counts.most_common(10)]
                
                # Ensure diversity
                unique_concepts = []
                seen_roots = set()
                for concept in top_concepts:
                    # Simple stemming
                    root = concept[:-3] if len(concept) > 4 and concept.endswith(('ing', 'ed')) else concept
                    if root not in seen_roots:
                        seen_roots.add(root)
                        unique_concepts.append(concept)
                
                return unique_concepts[:8]
            
            return []
            
        except Exception as e:
            logger.error(f"Concept extraction error: {e}")
            return []

    def detect_intent(self, text: str, context: List[str] = None) -> str:
        """Enhanced multilingual intent detection"""
        try:
            text = self.normalize_text(text)
            if not text:
                return "unknown"
            
            text_lower = text.lower()
            
            # Check for emojis first
            emoji_sentiment = self._analyze_emoji_sentiment(text)
            if emoji_sentiment > 0.5:
                return "positive_expression"
            elif emoji_sentiment < -0.5:
                return "negative_expression"
            
            # Question detection (multilingual)
            question_patterns = [
                r'^(what|why|how|when|where|who|which|can|could|would|should|is|are|do|does|did|will|won\'t|have|has|am|are|was|were)\s+\w+',
                r'^(kya|kaise|kab|kahan|kisne|kisko|kisliye|kis|kaun|kaunsa)\s+\w+',  # Hindi questions
                r'^(qué|cómo|cuándo|dónde|quién|cuál|por|para)\s+\w+',  # Spanish questions
                r'.*\?+$',
                r'^tell me about',
                r'^explain',
                r'^describe',
                r'^what do you think about',
                r'^how do you feel about',
                r'^can you explain',
                r'^could you tell me',
                r'^would you mind'
            ]
            
            for pattern in question_patterns:
                if re.match(pattern, text_lower, re.IGNORECASE):
                    return "question"
            
            # Greetings (multilingual)
            greeting_words = {
                'hello', 'hi', 'hey', 'sup', 'yo', 'greetings', 'good morning', 'good afternoon',
                'good evening', 'good night', 'namaste', 'salaam', 'hola', 'bonjour', 'ciao',
                'hiya', 'heya', 'wassup', 'whats up', 'howdy', 'yo yo', 'hey there', 'hi there'
            }
            
            if any(text_lower.startswith(greet) for greet in greeting_words):
                return "greeting"
            
            # Farewells
            farewell_words = {'bye', 'goodbye', 'see ya', 'see you', 'later', 'cya', 'adios', 'bye bye'}
            if any(text_lower.startswith(farewell) for farewell in farewell_words):
                return "farewell"
            
            # Requests
            request_patterns = [
                r'^(please|pls|plz|plis)\s+\w+',
                r'^(can|could|would|will) you\s+\w+',
                r'^(i need|i want|i\'d like|i would like)\s+\w+',
                r'^help me\s+\w*',
                r'^tell me\s+\w+',
                r'^show me\s+\w+',
                r'^give me\s+\w+',
                r'^explain to me\s+\w+',
                r'^teach me\s+\w+',
                r'^how do i\s+\w+'
            ]
            
            for pattern in request_patterns:
                if re.match(pattern, text_lower):
                    return "request"
            
            # Strong emotional expressions
            strong_emotion_patterns = [
                r'\b(love|hate|adore|despise|amazing|terrible|awesome|awful|fantastic|horrible)\b',
                r'\b(best|worst|perfect|disgusting|beautiful|ugly|brilliant|stupid)\b',
                r'\b(wow|omg|oh my god|holy shit|what the fuck|wtf)\b',
                r'\b(joy|happy|sad|angry|furious|ecstatic|depressed|anxious)\b'
            ]
            
            for pattern in strong_emotion_patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return "strong_expression"
            
            # Humor
            humor_patterns = [
                r'\b(lol+|lmao+|lmfao+|haha+|hehe+)\b',
                r'\b(funny|hilarious|comedy|joke|kidding|sarcasm|sarcastic)\b',
                r'\b(rofl|rotfl)\b'
            ]
            
            for pattern in humor_patterns:
                if re.search(pattern, text_lower):
                    return "humor"
            
            # Agreement/Disagreement
            if re.search(r'\b(yes|yeah|yep|sure|ok|okay|agree|right|correct|true)\b', text_lower):
                return "agreement"
            elif re.search(r'\b(no|nope|nah|wrong|incorrect|disagree|false)\b', text_lower):
                return "disagreement"
            
            # Information sharing
            info_indicators = [
                r'\bi think\b', r'\bi believe\b', r'\bin my opinion\b', r'\bfrom my perspective\b',
                r'\bthe way i see it\b', r'\bactually\b', r'\bbasically\b', r'\bfrankly\b',
                r'\bhonestly\b', r'\bpersonally\b', r'\btbh\b', r'\bimo\b'
            ]
            
            for indicator in info_indicators:
                if indicator in text_lower:
                    return "information"
            
            # Check if continuing conversation
            if context and len(context) > 0:
                # Check if this is a response to a question
                last_msg = context[-1] if isinstance(context[-1], str) else context[-1].get('content', '')
                if last_msg and '?' in last_msg and len(text.split()) < 15:
                    return "response"
                
                # Check if this continues the same topic
                last_concepts = self.extract_concepts(last_msg)
                current_concepts = self.extract_concepts(text)
                if last_concepts and current_concepts:
                    common = set(last_concepts) & set(current_concepts)
                    if len(common) > 0:
                        return "continuation"
            
            # Default based on text characteristics
            if len(text.split()) <= 3:
                if text.endswith('!'):
                    return "exclamation"
                elif text.endswith('...') or text.endswith('..'):
                    return "hesitation"
                else:
                    return "short_message"
            
            return "conversation"
            
        except Exception as e:
            logger.error(f"Intent detection error: {e}")
            return "unknown"

    def _analyze_emoji_sentiment(self, text: str) -> float:
        """Analyze sentiment from emojis"""
        try:
            # Find all emojis
            emoji_pattern = re.compile("["
                u"\U0001F600-\U0001F64F"  # emoticons
                u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                u"\U0001F680-\U0001F6FF"  # transport & map symbols
                u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                u"\U00002700-\U000027BF"  # Dingbats
                u"\U000024C2-\U0001F251" 
                "]+", flags=re.UNICODE)
            
            emojis = emoji_pattern.findall(text)
            if not emojis:
                return 0.0
            
            total_score = 0.0
            count = 0
            
            for emoji in emojis:
                if emoji in self.emoji_sentiment:
                    total_score += self.emoji_sentiment[emoji]
                    count += 1
            
            return total_score / count if count > 0 else 0.0
            
        except Exception as e:
            logger.error(f"Emoji sentiment error: {e}")
            return 0.0

    def analyze_sentiment(self, text: str) -> float:
        """Enhanced multilingual sentiment analysis"""
        try:
            text = self.normalize_text(text)
            if not text:
                return 0.0
            
            # Start with emoji sentiment
            emoji_score = self._analyze_emoji_sentiment(text)
            
            # Word-based sentiment (multilingual)
            positive_words = {
                'good', 'great', 'awesome', 'nice', 'love', 'happy', 'excellent', 'best',
                'wonderful', 'perfect', 'amazing', 'fantastic', 'brilliant', 'superb',
                'outstanding', 'fabulous', 'terrific', 'pleasant', 'joyful', 'delightful',
                'ecstatic', 'thrilled', 'overjoyed', 'pleased', 'content', 'satisfied',
                'grateful', 'thankful', 'blessed', 'lucky', 'fortunate', 'positive',
                'optimistic', 'hopeful', 'confident', 'proud', 'inspired', 'motivated',
                'energized', 'excited', 'enthusiastic', 'passionate', 'vibrant', 'alive',
                'fresh', 'new', 'improved', 'better', 'progress', 'success', 'victory',
                'win', 'achievement', 'accomplishment', 'milestone', 'breakthrough',
                'advancement', 'development', 'growth', 'expansion', 'increase', 'gain',
                'profit', 'benefit', 'advantage', 'opportunity', 'chance', 'possibility',
                'potential', 'promise', 'hope', 'dream', 'goal', 'ambition', 'aspiration'
            }
            
            negative_words = {
                'bad', 'terrible', 'awful', 'hate', 'sad', 'worst', 'horrible', 'dislike',
                'annoying', 'angry', 'upset', 'frustrated', 'disappointed', 'depressed',
                'anxious', 'stressed', 'worried', 'nervous', 'scared', 'afraid', 'fearful',
                'terrified', 'panicked', 'overwhelmed', 'exhausted', 'tired', 'fatigued',
                'drained', 'burned', 'out', 'sick', 'ill', 'unwell', 'pain', 'hurt',
                'injured', 'damaged', 'broken', 'ruined', 'destroyed', 'lost', 'missing',
                'gone', 'dead', 'died', 'death', 'kill', 'murder', 'violence', 'war',
                'conflict', 'fight', 'argument', 'dispute', 'quarrel', 'problem', 'issue',
                'trouble', 'difficulty', 'challenge', 'obstacle', 'barrier', 'setback',
                'failure', 'mistake', 'error', 'fault', 'blame', 'guilt', 'shame',
                'regret', 'remorse', 'sorry', 'apology', 'forgive', 'forgiveness'
            }
            
            # Hindi sentiment words (transliterated)
            hindi_positive = {'accha', 'bahut', 'khush', 'pyar', 'sundar', 'mast', 'jhakass', 'zabardast'}
            hindi_negative = {'bura', 'kharab', 'dikkat', 'problem', 'gussa', 'udaas', 'takleef', 'pareshan'}
            
            all_positive = positive_words | hindi_positive
            all_negative = negative_words | hindi_negative
            
            # Count sentiment words
            words = set(text.lower().split())
            positive_count = len(words.intersection(all_positive))
            negative_count = len(words.intersection(all_negative))
            
            # Check for negations
            negation_words = {'not', "n't", 'no', 'never', 'none', 'nothing', 'nowhere'}
            negation_adjacent = False
            
            word_list = text.lower().split()
            for i, word in enumerate(word_list):
                if word in negation_words and i < len(word_list) - 1:
                    next_word = word_list[i + 1]
                    if next_word in all_positive:
                        positive_count -= 1
                        negative_count += 1
                        negation_adjacent = True
                    elif next_word in all_negative:
                        negative_count -= 1
                        positive_count += 1
                        negation_adjacent = True
            
            # Calculate sentiment
            total = positive_count + negative_count
            if total == 0:
                word_based_score = 0.0
            else:
                word_based_score = (positive_count - negative_count) / total
            
            # Adjust for intensifiers
            intensifiers = {
                'very': 1.5, 'really': 1.5, 'extremely': 2.0, 'absolutely': 2.0,
                'completely': 1.8, 'totally': 1.8, 'utterly': 2.0, 'insanely': 1.7,
                'incredibly': 1.7, 'amazingly': 1.6, 'super': 1.4, 'mega': 1.4,
                'so': 1.3, 'too': 1.3, 'way': 1.3, 'quite': 1.2, 'pretty': 1.1,
                'bahut': 1.6, 'zyada': 1.5, 'bohot': 1.6  # Hindi intensifiers
            }
            
            intensity_multiplier = 1.0
            for intensifier, multiplier in intensifiers.items():
                if intensifier in text.lower():
                    intensity_multiplier *= multiplier
            
            # Check punctuation intensity
            if '!!!' in text or '???' in text:
                intensity_multiplier *= 1.3
            if '!!!!' in text or '????' in text:
                intensity_multiplier *= 1.5
            
            # Combine scores (weight emojis higher)
            combined_score = (word_based_score * 0.7) + (emoji_score * 0.3)
            adjusted_score = max(-1.0, min(1.0, combined_score * intensity_multiplier))
            
            # Apply smoothing
            if '...' in text or '..' in text:  # Hesitation reduces absolute sentiment
                adjusted_score *= 0.7
            
            return adjusted_score
            
        except Exception as e:
            logger.error(f"Sentiment analysis error: {e}")
            return 0.0

    @safe_execute
    async def understand_context(self, message) -> Dict[str, Any]:
        """Enhanced deep context understanding"""
        try:
            channel_id = message.channel.id
            user_id = message.author.id
            
            # Get recent conversation context
            recent_context = []
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT content, user_id, sentiment, intent, topics 
                    FROM episodic_memory 
                    WHERE channel_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (channel_id, self.attention_span)) as cursor:
                    recent_messages = await cursor.fetchall()
                    recent_context = [{
                        'content': row[0],
                        'user_id': row[1],
                        'sentiment': row[2],
                        'intent': row[3],
                        'topics': json.loads(row[4] or '[]')
                    } for row in recent_messages]
            
            # Extract concepts and analyze
            concepts = self.extract_concepts(message.content)
            intent = self.detect_intent(message.content, [ctx['content'] for ctx in recent_context[:3]])
            sentiment = self.analyze_sentiment(message.content)
            
            # Calculate message complexity
            word_count = len(message.content.split())
            complexity = min(1.0, word_count / 50)
            
            # Update user profile
            await self.update_user_profile(user_id, message, intent, sentiment)
            
            # Update channel vibe
            await self.update_channel_vibe(channel_id, message, intent, sentiment)
            
            # Determine conversation thread
            conversation_id = await self.identify_conversation_thread(message, recent_context)
            
            # Calculate message importance
            importance = self.calculate_importance(message, recent_context, concepts, intent, sentiment)
            
            # Build context info
            context_info = {
                'concepts': concepts,
                'intent': intent,
                'sentiment': sentiment,
                'recent_context': recent_context,
                'conversation_id': conversation_id,
                'importance': importance,
                'user_id': user_id,
                'channel_id': channel_id,
                'complexity': complexity,
                'word_count': word_count,
                'has_mention': self.bot.user in message.mentions,
                'message_hash': self.generate_message_hash(message.content, {
                    'concepts': concepts,
                    'timestamp': message.created_at.isoformat(),
                    'user_id': user_id
                }),
                'timestamp': message.created_at.isoformat()
            }
            
            self.stats['messages_processed'] += 1
            self.stats['conversations_understood'] += 1
            
            return context_info
            
        except Exception as e:
            logger.error(f"Context understanding error: {e}")
            await self._log_error("understand_context", str(e))
            return {
                'concepts': [],
                'intent': 'unknown',
                'sentiment': 0.0,
                'recent_context': [],
                'conversation_id': '',
                'importance': 0.5,
                'user_id': 0,
                'channel_id': 0,
                'complexity': 0.0,
                'word_count': 0,
                'has_mention': False,
                'message_hash': '',
                'timestamp': datetime.utcnow().isoformat()
            }

    @safe_execute
    async def update_user_profile(self, user_id: int, message, intent: str, sentiment: float):
        """Enhanced user profile updating"""
        try:
            username = str(message.author)
            async with aiosqlite.connect(self.db_path) as db:
                # Get existing profile
                async with db.execute('SELECT * FROM user_memory WHERE user_id = ?', (user_id,)) as cursor:
                    profile = await cursor.fetchone()
                
                if profile:
                    # Update existing profile
                    behavior = json.loads(profile[2] or '{}')
                    topics = json.loads(profile[3] or '[]')
                    style = json.loads(profile[4] or '{}')
                    response_prefs = json.loads(profile[10] or '{}')
                    
                    # Update behavior patterns
                    behavior[intent] = behavior.get(intent, 0) + 1
                    
                    # Update conversation style
                    msg_len = len(message.content.split())
                    style['avg_length'] = (style.get('avg_length', 0) * 0.9) + (msg_len * 0.1)
                    style['sentiment_avg'] = (style.get('sentiment_avg', 0) * 0.9) + (sentiment * 0.1)
                    style['response_time'] = style.get('response_time', 0) * 0.9 + 0.1
                    
                    # Update topics
                    concepts = self.extract_concepts(message.content)
                    for concept in concepts[:5]:
                        if concept not in topics:
                            topics.append(concept)
                    
                    # Update trust level
                    trust_delta = 0.02 if sentiment > 0.3 else -0.01 if sentiment < -0.3 else 0.005
                    new_trust = min(1.0, max(0.0, profile[6] + trust_delta))
                    
                    # Update relationship depth
                    interaction_count = profile[8] + 1
                    depth_increase = 0.01 * (1 + abs(sentiment))
                    new_depth = min(1.0, profile[9] + depth_increase)
                    
                    await db.execute('''
                        UPDATE user_memory SET 
                            username = ?,
                            behavior_pattern = ?,
                            preferred_topics = ?,
                            conversation_style = ?,
                            trust_level = ?,
                            last_interaction = ?,
                            interaction_count = ?,
                            sentiment_trend = sentiment_trend * 0.9 + ? * 0.1,
                            relationship_depth = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = ?
                    ''', (username, json.dumps(behavior), json.dumps(topics[:20]), 
                          json.dumps(style), new_trust, datetime.utcnow().isoformat(),
                          interaction_count, sentiment, new_depth, user_id))
                else:
                    # Create new profile
                    behavior = {intent: 1}
                    topics = self.extract_concepts(message.content)[:10]
                    style = {
                        'avg_length': len(message.content.split()),
                        'sentiment_avg': sentiment,
                        'formality': 0.3,
                        'response_time': 1.0,
                        'emoji_usage': 0.2
                    }
                    response_prefs = {}
                    
                    await db.execute('''
                        INSERT INTO user_memory 
                        (user_id, username, behavior_pattern, preferred_topics, 
                         conversation_style, trust_level, last_interaction, 
                         interaction_count, sentiment_trend, relationship_depth, response_preferences)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ''', (user_id, username, json.dumps(behavior), json.dumps(topics),
                          json.dumps(style), 0.5, datetime.utcnow().isoformat(),
                          sentiment, 0.1, json.dumps(response_prefs)))
                
                await db.commit()
                
                # Update cache
                self.user_profiles[user_id] = {
                    'trust': new_trust if profile else 0.5,
                    'interaction_count': interaction_count if profile else 1,
                    'last_interaction': datetime.utcnow()
                }
                
        except Exception as e:
            logger.error(f"User profile update error: {e}")

    @safe_execute
    async def update_channel_vibe(self, channel_id: int, message, intent: str, sentiment: float):
        """Enhanced channel vibe updating"""
        try:
            server_id = message.guild.id if message.guild else 0
            
            # Initialize vibe if needed
            if channel_id not in self.server_mood:
                self.server_mood[channel_id] = self.server_mood.default_factory()
            
            vibe = self.server_mood[channel_id]
            
            # Update energy (based on message frequency and length)
            energy_delta = 0.015 if len(message.content.split()) > 8 else 0.005
            vibe['energy'] = max(0, min(1, vibe['energy'] * 0.99 + energy_delta))
            
            # Update positivity with momentum
            vibe['positivity'] = (vibe['positivity'] * 0.97) + (max(-0.5, min(1.0, sentiment)) * 0.03)
            
            # Update coherence (topic consistency)
            concepts = self.extract_concepts(message.content)
            if concepts and vibe['topics']:
                # Check if continuing existing topics
                existing_topics = set(vibe['topics'].keys())
                current_topics = set(concepts)
                overlap = len(existing_topics.intersection(current_topics)) / max(1, len(current_topics))
                vibe['coherence'] = (vibe['coherence'] * 0.9) + (overlap * 0.1)
            
            # Update topics with decay
            for concept in concepts[:5]:
                vibe['topics'][concept] = vibe['topics'].get(concept, 0) * 0.95 + 1.0
            
            # Remove low-weight topics
            to_remove = [topic for topic, weight in vibe['topics'].items() if weight < 0.1]
            for topic in to_remove:
                del vibe['topics'][topic]
            
            # Track active hour
            hour = message.created_at.hour
            vibe['active_hours'][hour] = vibe['active_hours'].get(hour, 0) + 1
            
            # Track user engagement
            user_id = message.author.id
            vibe['user_engagement'][user_id] = vibe['user_engagement'].get(user_id, 0) * 0.9 + 1.0
            
            # Track interaction pattern
            vibe['interaction_patterns'][intent][user_id] += 1
            
            # Update conversation depth
            word_count = len(message.content.split())
            depth_delta = 0.01 if word_count > 15 else -0.005
            vibe['conversation_depth'] = max(0, min(1, vibe['conversation_depth'] + depth_delta))
            
            # Store in database periodically
            if self.stats['vibe_updates'] % 5 == 0:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute('''
                        INSERT OR REPLACE INTO server_vibes 
                        (server_id, channel_id, vibe_snapshot, topic_distribution, 
                         active_times, engagement_metrics, update_count)
                        VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT update_count FROM server_vibes 
                               WHERE server_id = ? AND channel_id = ?), 0) + 1)
                    ''', (server_id, channel_id, 
                          json.dumps({
                              'energy': vibe['energy'],
                              'positivity': vibe['positivity'],
                              'formality': vibe['formality'],
                              'coherence': vibe['coherence'],
                              'responsiveness': vibe['responsiveness'],
                              'conversation_depth': vibe['conversation_depth']
                          }),
                          json.dumps(dict(vibe['topics'])),
                          json.dumps(dict(vibe['active_hours'])),
                          json.dumps(dict(vibe['user_engagement'])),
                          server_id, channel_id))
                    await db.commit()
            
            self.stats['vibe_updates'] += 1
            
        except Exception as e:
            logger.error(f"Channel vibe update error: {e}")

    @safe_execute
    async def identify_conversation_thread(self, message, recent_context: List[Dict]) -> str:
        """Enhanced conversation thread identification"""
        try:
            if not recent_context:
                return self.generate_message_hash(message.content)
            
            # Check recent messages for continuity
            last_5_messages = recent_context[:5]
            
            # Check if same user continuing
            same_user_messages = [msg for msg in last_5_messages if msg['user_id'] == message.author.id]
            if same_user_messages:
                last_user_msg = same_user_messages[0]
                # Check topic overlap
                user_topics = set(last_user_msg.get('topics', []))
                current_topics = set(self.extract_concepts(message.content))
                overlap = len(user_topics.intersection(current_topics))
                
                if overlap > 0 or len(message.content.split()) < 10:
                    # Continue same conversation
                    async with aiosqlite.connect(self.db_path) as db:
                        async with db.execute('''
                            SELECT conversation_id FROM episodic_memory 
                            WHERE channel_id = ? AND user_id = ? 
                            ORDER BY timestamp DESC LIMIT 1
                        ''', (message.channel.id, message.author.id)) as cursor:
                            result = await cursor.fetchone()
                            if result:
                                return result[0]
            
            # Check if responding to bot
            bot_messages = [msg for msg in last_5_messages if msg['user_id'] == self.bot.user.id]
            if bot_messages and self.bot.user.id != message.author.id:
                last_bot_msg = bot_messages[0]
                # Check if this is a response to bot's question
                if last_bot_msg.get('intent') in ['question', 'request']:
                    async with aiosqlite.connect(self.db_path) as db:
                        async with db.execute('''
                            SELECT conversation_id FROM episodic_memory 
                            WHERE message_hash = ?
                        ''', (self.generate_message_hash(last_bot_msg['content']),)) as cursor:
                            result = await cursor.fetchone()
                            if result:
                                return result[0]
            
            # Start new conversation thread
            return self.generate_message_hash(f"{message.channel.id}_{message.author.id}_{datetime.utcnow().timestamp()}")
            
        except Exception as e:
            logger.error(f"Conversation thread identification error: {e}")
            return self.generate_message_hash(message.content)

    def calculate_importance(self, message, context: List[Dict], concepts: List[str], intent: str, sentiment: float) -> float:
        """Enhanced importance calculation"""
        try:
            importance = 0.5  # Base importance
            
            # Factor 1: Message length and complexity
            word_count = len(message.content.split())
            if word_count > 25:
                importance += 0.25
            elif word_count > 15:
                importance += 0.15
            elif word_count < 3:
                importance -= 0.2
            
            # Factor 2: Intent importance
            intent_weights = {
                'question': 0.3,
                'request': 0.25,
                'strong_expression': 0.2,
                'information': 0.15,
                'greeting': 0.05,
                'farewell': 0.05,
                'unknown': -0.1
            }
            importance += intent_weights.get(intent, 0.1)
            
            # Factor 3: Emotional intensity
            if abs(sentiment) > 0.7:
                importance += 0.2
            elif abs(sentiment) > 0.4:
                importance += 0.1
            
            # Factor 4: Unique concepts
            unique_concepts = len(set(concepts))
            importance += min(0.15, unique_concepts * 0.03)
            
            # Factor 5: User mention
            if self.bot.user in message.mentions:
                importance += 0.3
            
            # Factor 6: Response to bot
            if context and context[0].get('user_id') == self.bot.user.id:
                importance += 0.15
            
            # Factor 7: Contains links or code
            if 'http://' in message.content or 'https://' in message.content:
                importance += 0.1
            if '```' in message.content:
                importance += 0.15
            
            # Factor 8: Capitalization (shouting)
            if len(message.content) > 10 and message.content.isupper():
                importance += 0.1
            
            return max(0.05, min(1.0, importance))
            
        except Exception as e:
            logger.error(f"Importance calculation error: {e}")
            return 0.5


    def understand_hinglish(self, text: str) -> Dict[str, Any]:
        """Actually understand Hinglish conversations"""
        text_lower = text.lower()
        
        # Common Hinglish patterns
        hinglish_patterns = {
            # Questions
            r'kya.*hai': 'what is',
            r'kaise.*ho': 'how are you',
            r'kab.*aaya': 'when did you come',
            r'kahan.*hai': 'where is',
            r'kyun.*nahi': 'why not',
            
            # Expressions
            r'arre.*yaar': 'oh dude',
            r'oh.*bhai': 'oh brother',
            r'mast.*hai': 'is awesome',
            r'jhakass.*hai': 'is amazing',
            r'bekar.*hai': 'is useless',
            
            # Mixed sentences
            r'(\w+)\s+(hai|hain|ho|tha|thi|the)\s+(\w+)': self._translate_hinglish_verb
        }
        
        # Check if it's Hinglish
        is_hinglish = False
        for pattern in hinglish_patterns.keys():
            if re.search(pattern, text_lower):
                is_hinglish = True
                break
        
        if not is_hinglish:
            return {'is_hinglish': False, 'translation': text}
        
        # Translate common Hinglish phrases
        translation = text_lower
        
        # Simple word mappings
        hinglish_words = {
            'kya': 'what', 'kaise': 'how', 'kab': 'when', 'kahan': 'where',
            'kyun': 'why', 'kaun': 'who', 'kisne': 'who did', 'kisko': 'to whom',
            'hai': 'is', 'hain': 'are', 'ho': 'are', 'tha': 'was', 'thi': 'was',
            'the': 'were', 'bhai': 'bro', 'yaar': 'dude', 'dost': 'friend',
            'pagal': 'crazy', 'mast': 'awesome', 'jhakass': 'amazing',
            'bekar': 'useless', 'accha': 'good', 'kharab': 'bad',
            'timepass': 'timepass', 'ghanta': 'nonsense', 'bhidu': 'dude',
            'boss': 'boss', 'sahi': 'correct', 'galat': 'wrong'
        }
        
        words = translation.split()
        translated_words = []
        for word in words:
            if word in hinglish_words:
                translated_words.append(hinglish_words[word])
                is_hinglish = True
            else:
                translated_words.append(word)
        
        translation = ' '.join(translated_words)
        
        # Extract Hinglish emotion
        hinglish_emotions = {
            'yaar': 0.3, 'bhai': 0.4, 'mast': 0.8, 'jhakass': 0.9,
            'bekar': -0.6, 'pagal': -0.3, 'ghanta': -0.7, 'timepass': 0.1,
            'accha': 0.7, 'kharab': -0.7, 'sahi': 0.6, 'galat': -0.6
        }
        
        emotion_score = 0.0
        emotion_words = []
        for word, score in hinglish_emotions.items():
            if word in text_lower:
                emotion_score += score
                emotion_words.append(word)
        
        if emotion_words:
            emotion_score = emotion_score / len(emotion_words)
        
        return {
            'is_hinglish': True,
            'original': text,
            'translation': translation,
            'emotion_score': emotion_score,
            'emotion_words': emotion_words,
            'language_mix': 'hindi_english'
        }
    
    def _translate_hinglish_verb(self, match):
        """Translate Hinglish verb patterns"""
        subject = match.group(1)
        verb = match.group(2)
        object_word = match.group(3)
        
        verb_map = {
            'hai': 'is', 'hain': 'are', 'ho': 'are',
            'tha': 'was', 'thi': 'was', 'the': 'were'
        }
        
        translated_verb = verb_map.get(verb, verb)
        return f"{subject} {translated_verb} {object_word}"


    @safe_execute
    async def store_memory(self, message, context_info: Dict):
        """Enhanced memory storage"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Store episodic memory
                await db.execute('''
                    INSERT OR REPLACE INTO episodic_memory 
                    (channel_id, user_id, message_hash, content, timestamp, 
                     context_before, context_after, topics, sentiment, 
                     intent, importance, conversation_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    context_info['channel_id'],
                    context_info['user_id'],
                    context_info['message_hash'],
                    message.content,
                    message.created_at.isoformat(),
                    json.dumps([msg['content'] for msg in context_info['recent_context'][:3]]),
                    '[]',
                    json.dumps(context_info['concepts']),
                    context_info['sentiment'],
                    context_info['intent'],
                    context_info['importance'],
                    context_info['conversation_id'],
                    json.dumps({
                        'word_count': context_info['word_count'],
                        'complexity': context_info['complexity'],
                        'has_mention': context_info['has_mention']
                    })
                ))
                
                # Update semantic memory
                for concept in context_info['concepts'][:5]:
                    concept_hash = hashlib.md5(concept.encode()).hexdigest()
                    
                    # Get existing concept
                    async with db.execute('SELECT * FROM semantic_memory WHERE concept_hash = ?', (concept_hash,)) as cursor:
                        existing = await cursor.fetchone()
                    
                    if existing:
                        # Update existing concept
                        associations = json.loads(existing[2] or '[]')
                        context_tags = json.loads(existing[7] or '[]')
                        
                        # Add new context if not present
                        if context_info['intent'] not in context_tags:
                            context_tags.append(context_info['intent'])
                        
                        await db.execute('''
                            UPDATE semantic_memory SET
                                strength = MIN(10.0, strength + 0.2),
                                access_count = access_count + 1,
                                last_accessed = CURRENT_TIMESTAMP,
                                emotional_weight = (emotional_weight * 0.9) + (? * 0.1),
                                context_tags = ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE concept_hash = ?
                        ''', (context_info['sentiment'], json.dumps(context_tags[:10]), concept_hash))
                    else:
                        # Create new concept
                        await db.execute('''
                            INSERT INTO semantic_memory 
                            (concept_hash, concept, associations, strength, access_count, 
                             emotional_weight, context_tags, language)
                            VALUES (?, ?, ?, 1.0, 1, ?, ?, ?)
                        ''', (concept_hash, concept, json.dumps([]), 
                              context_info['sentiment'], 
                              json.dumps([context_info['intent']]),
                              'en'))
                
                await db.commit()
                self.stats['memory_recalls'] += 1
                
        except Exception as e:
            logger.error(f"Memory storage error: {e}")

    @rate_limit(max_calls=3, period=10)
    @safe_execute
    async def generate_conscious_reply(self, message, context_info: Dict) -> Optional[str]:
        """REAL conscious reply generation - thinks about conversation"""
        try:
            # 1. Get conversation memories
            relevant_memories = await self.recall_relevant_memories(message, context_info)
            
            # 2. Build enhanced understanding
            conversation_context = await self._build_conversation_context(
                message, context_info, relevant_memories, message.author.display_name
            )
            
            # 3. Decide if we should reply (like a human would)
            should_reply, reply_style = await self._decide_to_reply(conversation_context)
            
            if not should_reply:
                # Sometimes the most human thing is to not reply
                return None
            
            # 4. Generate response with actual thinking
            reply = await self.construct_response(
                message, context_info, relevant_memories, 
                reply_style, message.author.display_name
            )
            
            if not reply or len(reply.strip()) == 0:
                return None
            
            # 5. Ensure safe length
            reply = reply[:self.max_response_length]
            
            # 6. Update consciousness based on REAL conversation
            self._update_conversation_intelligence(conversation_context, reply)
            
            self.stats['context_aware_replies'] += 1
            
            return reply
            
        except Exception as e:
            logger.error(f"Conscious reply generation error: {e}")
            self.stats['failed_responses'] += 1
            await self._log_error("generate_reply", str(e))
            return None

    async def _decide_to_reply(self, context: ConversationContext) -> Tuple[bool, str]:
        """Decide if and how to reply like a human"""
        # Always reply if directly mentioned
        if context.is_directed_at_me:
            return True, 'direct'
        
        # Check conversation history
        conversation_key = f"{context.channel_id}_{context.user_id}"
        recent_replies = self.conversation_history.get(conversation_key, deque(maxlen=5))
        
        # Don't reply too much to same person
        if len(recent_replies) > 3:
            return False, 'silent'
        
        # Base probability
        base_chance = 0.2
        
        # Increase if they asked a question
        if context.contains_question:
            base_chance = 0.7
        
        # Increase if emotional
        if abs(context.sentiment) > 0.6:
            base_chance = 0.6
        
        # Decrease if vibe is low energy
        if context.vibe.get('energy', 0.5) < 0.3:
            base_chance *= 0.5
        
        # Decide
        if random.random() < base_chance:
            # Choose style
            if context.sentiment > 0.5:
                style = 'positive'
            elif context.sentiment < -0.5:
                style = 'empathetic'
            elif context.contains_question:
                style = 'helpful'
            else:
                style = 'conversational'
            
            return True, style
        
        return False, 'silent'

    def _update_conversation_intelligence(self, context: ConversationContext, reply: str):
        """Update bot's understanding of conversation"""
        # Track this conversation
        conversation_key = f"{context.channel_id}_{context.user_id}"
        self.conversation_history[conversation_key].append({
            'timestamp': datetime.utcnow(),
            'user_message': context.raw_message[:100],
            'bot_reply': reply[:100],
            'sentiment': context.sentiment,
            'goal': context.goal.value
        })
        
        # Update emotional state based on REAL interaction
        if context.sentiment < -0.5:
            self.consciousness['emotional_state']['empathy'] = min(1.0, 
                self.consciousness['emotional_state']['empathy'] + 0.1
            )
        elif context.sentiment > 0.5:
            self.consciousness['emotional_state']['valence'] = min(1.0,
                self.consciousness['emotional_state']['valence'] + 0.05
            )
        
        # Update cognitive load based on conversation depth
        if context.conversation_depth > 0.7:
            self.consciousness['cognitive_load'] = min(1.0,
                self.consciousness['cognitive_load'] + 0.02
            )

    @safe_execute
    async def recall_relevant_memories(self, message, context_info: Dict) -> List[Dict]:
        """Enhanced memory recall with multiple strategies"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                relevant_memories = []
                
                # Strategy 1: Recall by conversation thread (most recent)
                async with db.execute('''
                    SELECT content, sentiment, intent, topics, importance
                    FROM episodic_memory 
                    WHERE conversation_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT 8
                ''', (context_info['conversation_id'],)) as cursor:
                    conversation_memories = await cursor.fetchall()
                    for mem in conversation_memories:
                        relevant_memories.append({
                            'type': 'conversation',
                            'content': mem[0],
                            'sentiment': mem[1],
                            'intent': mem[2],
                            'topics': json.loads(mem[3] or '[]'),
                            'importance': mem[4],
                            'recency': 1.0
                        })
                
                # Strategy 2: Recall by semantic similarity (concepts)
                for concept in context_info['concepts'][:4]:
                    async with db.execute('''
                        SELECT em.content, em.sentiment, em.intent, em.importance
                        FROM episodic_memory em
                        WHERE em.topics LIKE ? 
                        AND em.importance > 0.3
                        ORDER BY em.importance DESC, em.timestamp DESC
                        LIMIT 2
                    ''', (f'%{concept}%',)) as cursor:
                        semantic_memories = await cursor.fetchall()
                        for mem in semantic_memories:
                            relevant_memories.append({
                                'type': 'semantic',
                                'content': mem[0],
                                'sentiment': mem[1],
                                'intent': mem[2],
                                'importance': mem[3],
                                'concept': concept,
                                'recency': 0.7
                            })
                
                # Strategy 3: Recall by user history
                async with db.execute('''
                    SELECT content, intent, sentiment, importance, timestamp
                    FROM episodic_memory 
                    WHERE user_id = ? 
                    AND sentiment * ? > 0  # Same sentiment direction
                    ORDER BY importance DESC, timestamp DESC
                    LIMIT 4
                ''', (context_info['user_id'], context_info['sentiment'])) as cursor:
                    user_memories = await cursor.fetchall()
                    for mem in user_memories:
                        relevant_memories.append({
                            'type': 'user',
                            'content': mem[0],
                            'intent': mem[1],
                            'sentiment': mem[2],
                            'importance': mem[3],
                            'recency': 0.8
                        })
                
                # Strategy 4: Recall similar intents
                async with db.execute('''
                    SELECT content, sentiment, topics
                    FROM episodic_memory 
                    WHERE intent = ? 
                    AND ABS(sentiment - ?) < 0.5
                    AND channel_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 3
                ''', (context_info['intent'], context_info['sentiment'], context_info['channel_id'])) as cursor:
                    intent_memories = await cursor.fetchall()
                    for mem in intent_memories:
                        relevant_memories.append({
                            'type': 'intent',
                            'content': mem[0],
                            'sentiment': mem[1],
                            'topics': json.loads(mem[2] or '[]'),
                            'recency': 0.6
                        })
                
                # Deduplicate and sort by relevance score
                unique_memories = {}
                for mem in relevant_memories:
                    key = (mem['type'], mem.get('content', '')[:50])
                    if key not in unique_memories:
                        unique_memories[key] = mem
                    else:
                        # Keep the one with higher recency
                        if mem.get('recency', 0) > unique_memories[key].get('recency', 0):
                            unique_memories[key] = mem
                
                # Calculate relevance score and sort
                scored_memories = []
                for mem in unique_memories.values():
                    score = mem.get('importance', 0.5) * 0.4 + mem.get('recency', 0.5) * 0.6
                    if mem['type'] == 'conversation':
                        score *= 1.2
                    scored_memories.append((score, mem))
                
                scored_memories.sort(reverse=True, key=lambda x: x[0])
                return [mem for _, mem in scored_memories[:12]]  # Limit to 12 most relevant
                
        except Exception as e:
            logger.error(f"Memory recall error: {e}")
            return []

    @safe_execute
    async def get_user_profile(self, user_id: int) -> Dict:
        """Get enhanced user profile"""
        try:
            # Check cache first
            if user_id in self.user_profiles:
                cached = self.user_profiles[user_id]
                if (datetime.utcnow() - cached['last_interaction']).total_seconds() < 300:
                    return cached
            
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT behavior_pattern, preferred_topics, conversation_style,
                           trust_level, interaction_count, sentiment_trend,
                           relationship_depth, response_preferences
                    FROM user_memory WHERE user_id = ?
                ''', (user_id,)) as cursor:
                    profile = await cursor.fetchone()
                
                if profile:
                    result = {
                        'behavior': json.loads(profile[0] or '{}'),
                        'topics': json.loads(profile[1] or '[]'),
                        'style': json.loads(profile[2] or '{}'),
                        'trust': profile[3],
                        'interaction_count': profile[4],
                        'sentiment_trend': profile[5],
                        'relationship_depth': profile[6],
                        'response_preferences': json.loads(profile[7] or '{}'),
                        'last_interaction': datetime.utcnow()
                    }
                    
                    # Update cache
                    self.user_profiles[user_id] = result
                    return result
            
            return {}
            
        except Exception as e:
            logger.error(f"User profile fetch error: {e}")
            return {}

    def determine_response_style(self, context: Dict, vibe: Dict, user_profile: Dict) -> Dict:
        """Enhanced response style determination"""
        style = {
            'formality': 0.3,
            'enthusiasm': 0.5,
            'depth': 0.5,
            'humor': 0.2,
            'empathy': 0.5,
            'brevity': 0.5,
            'technicality': 0.1,
            'creativity': 0.3
        }
        
        # Adjust based on channel vibe
        if vibe:
            style['formality'] = vibe.get('formality', 0.3)
            style['enthusiasm'] = vibe.get('energy', 0.5)
            style['depth'] = vibe.get('conversation_depth', 0.5)
            style['creativity'] = vibe.get('coherence', 0.5) * 0.8
            
            # Higher coherence allows for more creative responses
            if vibe.get('coherence', 0.5) > 0.7:
                style['creativity'] += 0.2
        
        # Adjust based on user profile
        if user_profile:
            user_style = user_profile.get('style', {})
            style['formality'] = (style['formality'] + user_style.get('formality', 0.3)) / 2
            
            # Higher trust = more empathy and depth
            trust = user_profile.get('trust', 0.5)
            style['empathy'] = min(1.0, style['empathy'] + (trust * 0.4))
            style['depth'] = min(1.0, style['depth'] + (trust * 0.3))
            
            # More interactions = more personalized
            interactions = user_profile.get('interaction_count', 0)
            if interactions > 20:
                style['personalization'] = 0.8
            elif interactions > 5:
                style['personalization'] = 0.5
            else:
                style['personalization'] = 0.2
        
        # Adjust based on message intent
        intent = context['intent']
        if intent == 'question':
            style['depth'] = 0.8
            style['brevity'] = 0.3  # Questions deserve detailed answers
        elif intent == 'request':
            style['empathy'] = 0.7
            style['brevity'] = 0.7  # Requests should be clear and concise
        elif intent in ['strong_expression', 'positive_expression', 'negative_expression']:
            style['empathy'] = 0.9
            style['enthusiasm'] = 0.7 if 'positive' in intent else 0.3
        elif intent == 'humor':
            style['humor'] = 0.8
            style['creativity'] = 0.7
        
        # Adjust based on sentiment
        sentiment = context['sentiment']
        if sentiment > 0.6:
            style['enthusiasm'] = 0.9
            style['humor'] = min(1.0, style['humor'] + 0.3)
        elif sentiment < -0.6:
            style['empathy'] = 0.9
            style['humor'] = 0.0
            style['enthusiasm'] = 0.2
        
        # Adjust based on complexity
        complexity = context.get('complexity', 0.5)
        if complexity > 0.7:
            style['depth'] = 0.9
            style['technicality'] = 0.4
        elif complexity < 0.3:
            style['brevity'] = 0.8
            style['depth'] = 0.3
        
        # Ensure all values are within bounds
        for key in style:
            style[key] = max(0.0, min(1.0, style[key]))
        
        return style

    async def _generate_greeting_response(self, message, context, memories, style, user_name):
        """Generate greeting response"""
        greetings = [
            f"Hey {user_name}! 👋 How's everything going?",
            f"Hello {user_name}! Good to see you!",
            f"Hi there {user_name}! What's on your mind today?",
            f"Hey {user_name}! How's your day been so far?",
            f"Greetings {user_name}! 👋 Nice to chat with you!",
            f"Hi {user_name}! 😊 What brings you here today?",
            f"Hey {user_name}! Hope you're having a good one!"
        ]
        
        # Check for time of day
        hour = datetime.utcnow().hour
        if 5 <= hour < 12:
            greetings.extend([
                f"Good morning {user_name}! ☀️",
                f"Morning {user_name}! Ready for the day?"
            ])
        elif 12 <= hour < 17:
            greetings.extend([
                f"Good afternoon {user_name}!",
                f"Afternoon {user_name}! How's your day going?"
            ])
        elif 17 <= hour < 22:
            greetings.extend([
                f"Good evening {user_name}! 🌙",
                f"Evening {user_name}! How was your day?"
            ])
        else:
            greetings.extend([
                f"Late night for you too, {user_name}? 🌃",
                f"Hi {user_name}! Burning the midnight oil?"
            ])
        
        # Check if we have recent history with user
        user_memories = [m for m in memories if m['type'] == 'user']
        if user_memories:
            last_memory = user_memories[0]
            if last_memory.get('sentiment', 0) > 0.3:
                return f"Hey {user_name}! Good to see you again! I remember our last chat was quite positive. 😊"
            elif 'content' in last_memory:
                # Reference previous conversation
                concepts = context['concepts'][:2]
                if concepts:
                    return f"Hi {user_name}! We were discussing {', '.join(concepts)} last time. Want to continue?"
        
        return random.choice(greetings)

    async def _generate_question_response(self, message, context, memories, style, user_name):
        """Generate response to questions"""
        # Try to find relevant information in memories
        relevant_info = []
        question_concepts = set(context['concepts'])
        
        for memory in memories:
            if memory['type'] in ['semantic', 'conversation', 'intent']:
                # Check topic overlap
                memory_topics = set(memory.get('topics', []))
                overlap = len(question_concepts.intersection(memory_topics))
                
                if overlap > 0 or memory['type'] == 'conversation':
                    relevant_info.append(memory)
        
        if relevant_info:
            # Sort by relevance
            relevant_info.sort(key=lambda x: (
                x.get('importance', 0.5) * 0.5 + 
                x.get('recency', 0.5) * 0.5
            ), reverse=True)
            
            best_memory = relevant_info[0]
            
            # Generate response based on memory
            responses = [
                f"Based on what I remember, {best_memory['content'].lower()}",
                f"I recall that {best_memory['content'].lower()}",
                f"From previous discussions, {best_memory['content'].lower()}",
                f"Interesting question! I think {best_memory['content'].lower()}",
                f"Good question! {best_memory['content'].capitalize()}"
            ]
            
            response = random.choice(responses)
            
            # Add uncertainty if sentiment is neutral
            if abs(best_memory.get('sentiment', 0)) < 0.3 and random.random() < 0.3:
                response += " But I'm not entirely sure about that."
            
            return response
        
        # No relevant memories - generate thoughtful response
        generic_responses = [
            f"That's an interesting question, {user_name}. What are your thoughts on it?",
            f"I'm not entirely sure about that, but I'd love to hear your perspective!",
            f"Hmm, that makes me think. What's your take on it, {user_name}?",
            f"Good question! I'm still learning about that topic.",
            f"I don't have a definite answer, but here's what I think: it could be worth discussing further.",
            f"That's a thoughtful question. Let me ponder it for a moment...",
            f"I appreciate you asking that, {user_name}. It's something worth exploring together."
        ]
        
        # Add topic-specific responses if we have concepts
        if context['concepts']:
            topic = context['concepts'][0]
            topic_responses = [
                f"Regarding {topic}, I think it's a fascinating subject. What specifically interests you about it?",
                f"{topic.capitalize()} is quite complex. What aspect are you most curious about?",
                f"Good question about {topic}! I've been learning more about that recently."
            ]
            generic_responses.extend(topic_responses)
        
        return random.choice(generic_responses)

    async def _generate_request_response(self, message, context, memories, style, user_name):
        """Generate response to requests"""
        # Check if we can help based on memories
        relevant_memories = [m for m in memories if m['type'] in ['semantic', 'user']]
        
        if relevant_memories:
            memory = random.choice(relevant_memories[:3])
            
            responses = [
                f"I think I can help with that! {memory['content'].capitalize()}",
                f"Based on what I know, {memory['content'].lower()}",
                f"I remember something related: {memory['content'].lower()}",
                f"Let me see... {memory['content'].capitalize()}",
                f"Regarding your request, {memory['content'].lower()}"
            ]
            
            return random.choice(responses)
        
        # Generic helpful response
        responses = [
            f"I'll do my best to help with that, {user_name}!",
            f"Let me see what I can do about that request.",
            f"I understand you're asking for help. Let me process that.",
            f"I'll try to assist with that, {user_name}!",
            f"Got it. I'm on it, {user_name}!"
        ]
        
        return random.choice(responses)

    async def _generate_positive_response(self, message, context, memories, style, user_name):
        """Generate response to positive expressions"""
        sentiment = context['sentiment']
        
        if sentiment > 0.8:
            responses = [
                f"That's absolutely fantastic, {user_name}! 🎉",
                f"Wow, that's amazing news! So happy for you! 😄",
                f"Incredible! That's wonderful to hear! 🌟",
                f"That's absolutely brilliant! 🥳",
                f"Outstanding! That's so great! ✨"
            ]
        elif sentiment > 0.5:
            responses = [
                f"That's awesome, {user_name}! 😊",
                f"Glad to hear that! 👍",
                f"That's great news!",
                f"Nice! Happy for you!",
                f"That's wonderful! 😄"
            ]
        else:
            responses = [
                f"Good to hear!",
                f"That's positive!",
                f"Nice one!",
                f"Glad things are going well!",
                f"That's encouraging!"
            ]
        
        # Add memory reference if available
        positive_memories = [m for m in memories if m.get('sentiment', 0) > 0.3]
        if positive_memories and random.random() < 0.4:
            memory = random.choice(positive_memories[:2])
            return f"{random.choice(responses)} You often share positive things like this!"
        
        return random.choice(responses)

    async def _generate_negative_response(self, message, context, memories, style, user_name):
        """Generate response to negative expressions"""
        sentiment = context['sentiment']
        
        if sentiment < -0.8:
            responses = [
                f"I'm so sorry to hear that, {user_name}. That sounds really tough. 💔",
                f"That must be incredibly difficult. I'm here for you. 🤗",
                f"My heart goes out to you, {user_name}. That's really hard.",
                f"I can't imagine how tough that must be. Sending support your way. ❤️",
                f"That's heartbreaking to hear. Please know I'm here if you need to talk. 🙏"
            ]
        elif sentiment < -0.5:
            responses = [
                f"I'm sorry to hear that, {user_name}.",
                f"That sounds difficult. 😔",
                f"I understand how that might feel.",
                f"That must be challenging.",
                f"I'm here if you want to talk about it."
            ]
        else:
            responses = [
                f"I see. Thanks for sharing that.",
                f"I understand.",
                f"Got it.",
                f"Thanks for letting me know.",
                f"I appreciate you sharing that."
            ]
        
        # Add empathetic follow-up
        if sentiment < -0.5 and random.random() < 0.6:
            follow_ups = [
                " How are you holding up?",
                " Is there anything I can do to help?",
                " Would it help to talk about it more?",
                " Remember to take care of yourself.",
                " I'm listening if you need to vent."
            ]
            return random.choice(responses) + random.choice(follow_ups)
        
        return random.choice(responses)

    async def _generate_conversational_response(self, message, context, memories, style, user_name):
        """Generate general conversational response"""
        # Try to continue existing conversation
        conversation_memories = [m for m in memories if m['type'] == 'conversation']
        
        if conversation_memories and len(conversation_memories) > 1:
            # Continue thread
            connectors = [
                "Speaking of that,",
                "Related to what we were discussing,",
                "That reminds me,",
                "Building on that point,",
                "Continuing that thought,"
            ]
            
            follow_ups = [
                f"what do you think about {context['concepts'][0] if context['concepts'] else 'this'}?",
                f"how do you see {context['concepts'][0] if context['concepts'] else 'things'} developing?",
                f"I'm curious about your perspective on {context['concepts'][0] if context['concepts'] else 'this topic'}.",
                f"that's an interesting angle on {context['concepts'][0] if context['concepts'] else 'things'}."
            ]
            
            return f"{random.choice(connectors)} {random.choice(follow_ups)}"
        
        # Start new conversation thread
        if context['concepts']:
            topic = context['concepts'][0]
            starters = [
                f"What are your thoughts on {topic}, {user_name}?",
                f"I find {topic} interesting. What about you?",
                f"Have you been thinking about {topic} lately?",
                f"{topic.capitalize()} is a fascinating subject. What draws you to it?",
                f"Regarding {topic}, what's your take on it?"
            ]
        else:
            starters = [
                f"What's on your mind, {user_name}?",
                f"How's everything going for you?",
                f"What have you been up to lately?",
                f"Anything interesting happening in your world?",
                f"How's your day shaping up?"
            ]
        
        return random.choice(starters)

    def _apply_response_style(self, response: str, style: Dict) -> str:
        """Apply stylistic adjustments to response"""
        if not response:
            return response
        
        # Adjust formality
        if style['formality'] > 0.7:
            # Make more formal
            informal_pairs = [
                ("what's", "what is"), ("that's", "that is"), ("it's", "it is"),
                ("i'm", "I am"), ("you're", "you are"), ("they're", "they are"),
                ("we're", "we are"), ("can't", "cannot"), ("won't", "will not"),
                ("don't", "do not"), ("doesn't", "does not"), ("isn't", "is not"),
                ("aren't", "are not"), ("wasn't", "was not"), ("weren't", "were not"),
                ("haven't", "have not"), ("hasn't", "has not"), ("hadn't", "had not"),
                ("wouldn't", "would not"), ("couldn't", "could not"), ("shouldn't", "should not")
            ]
            
            for informal, formal in informal_pairs:
                response = response.replace(informal, formal)
                response = response.replace(informal.capitalize(), formal.capitalize())
            
            # Add formal openings/closings sometimes
            if random.random() < 0.3:
                formal_openings = ["I would like to mention that", "It is worth noting that", "I should point out that"]
                if not any(opening in response for opening in formal_openings):
                    response = f"{random.choice(formal_openings)} {response.lower()}"
        
        elif style['formality'] < 0.3:
            # Make more casual
            if random.random() < 0.4:
                casual_prefixes = ["Yeah,", "So,", "Well,", "Hey,", "So like,", "I mean,"]
                response = f"{random.choice(casual_prefixes)} {response.lower()}"
            
            # Use contractions
            formal_pairs = [
                ("what is", "what's"), ("that is", "that's"), ("it is", "it's"),
                ("I am", "I'm"), ("you are", "you're"), ("they are", "they're"),
                ("we are", "we're"), ("cannot", "can't"), ("will not", "won't"),
                ("do not", "don't"), ("does not", "doesn't"), ("is not", "isn't"),
                ("are not", "aren't"), ("was not", "wasn't"), ("were not", "weren't")
            ]
            
            for formal, informal in formal_pairs:
                response = response.replace(formal, informal)
                response = response.replace(formal.capitalize(), informal.capitalize())
        
        # Adjust enthusiasm
        if style['enthusiasm'] > 0.7:
            # Add exclamation and emojis
            if random.random() < 0.6:
                response = response.replace('.', '!')
                response = response.replace('?', '?!')
            
            if random.random() < 0.4:
                enthusiastic_emojis = [' 😊', ' 😄', ' 👍', ' ✨', ' 🎉', ' 🌟', ' 🔥']
                response += random.choice(enthusiastic_emojis)
        
        elif style['enthusiasm'] < 0.3:
            # Make more subdued
            response = response.replace('!', '.')
            if random.random() < 0.3:
                subdued_words = ['perhaps', 'maybe', 'possibly', 'kind of', 'sort of']
                if len(response.split()) > 5:
                    words = response.split()
                    insert_pos = random.randint(1, len(words)-1)
                    words.insert(insert_pos, random.choice(subdued_words))
                    response = ' '.join(words)
        
        # Add humor
        if style['humor'] > 0.5 and random.random() < style['humor']:
            humor_suffixes = [' lol', ' haha', ' 😂', ' 😆', ' :D']
            if not response.endswith(tuple(humor_suffixes)):
                response += random.choice(humor_suffixes)
        
        # Adjust brevity
        if style['brevity'] > 0.7 and len(response.split()) > 15:
            # Try to shorten
            words = response.split()
            if len(words) > 20:
                # Keep only the core message
                response = ' '.join(words[:15]) + '...'
        
        return response.strip()

    def _personalize_response(self, response: str, context: Dict, memories: List[Dict]) -> str:
        """Add personalization to response"""
        user_memories = [m for m in memories if m['type'] == 'user']
        
        if user_memories:
            memory = random.choice(user_memories[:2])
            
            # Add memory reference
            references = [
                " I remember you mentioned something similar before.",
                " This reminds me of our previous conversation.",
                " You often have interesting thoughts on this.",
                " I recall you sharing something related earlier."
            ]
            
            if random.random() < 0.5:
                response += random.choice(references)
        
        return response

    def _sanitize_response(self, response: str) -> str:
        """Ensure response is safe and appropriate"""
        if not response:
            return response
        
        # Remove any potential code injection
        response = response.replace('```', '')
        response = response.replace('`', "'")
        
        # Limit multiple exclamation/question marks
        response = re.sub(r'!{3,}', '!!', response)
        response = re.sub(r'\?{3,}', '??', response)
        
        # Remove excessive whitespace
        response = ' '.join(response.split())
        
        # Ensure proper capitalization
        if len(response) > 1:
            response = response[0].upper() + response[1:]
        
        return response.strip()

    def adjust_emotional_state(self, sentiment: float, message, reply: str = None):
        """Adjust bot's emotional state based on interaction"""
        try:
            # Update valence (positive/negative)
            self.consciousness['emotional_state']['valence'] = max(0.0, min(1.0,
                self.consciousness['emotional_state']['valence'] * 0.92 + 
                ((sentiment + 1) / 2) * 0.08
            ))
            
            # Update arousal based on message characteristics
            msg_length = len(message.content.split())
            reply_length = len(reply.split()) if reply else 0
            interaction_length = msg_length + reply_length
            
            arousal_delta = min(0.15, interaction_length / 200)
            self.consciousness['emotional_state']['arousal'] = max(0.0, min(1.0,
                self.consciousness['emotional_state']['arousal'] * 0.94 + arousal_delta
            ))
            
            # Update confidence based on response quality
            if reply:
                reply_sentiment = self.analyze_sentiment(reply)
                confidence_delta = 0.05 if abs(reply_sentiment - sentiment) < 0.5 else -0.03
                self.consciousness['emotional_state']['confidence'] = max(0.1, min(1.0,
                    self.consciousness['emotional_state']['confidence'] * 0.95 + confidence_delta
                ))
            
            # Update empathy based on user sentiment
            empathy_delta = 0.02 if sentiment < -0.3 else 0.01 if sentiment > 0.3 else 0.005
            self.consciousness['emotional_state']['empathy'] = max(0.1, min(1.0,
                self.consciousness['emotional_state']['empathy'] * 0.98 + empathy_delta
            ))
            
        except Exception as e:
            logger.error(f"Emotional state adjustment error: {e}")

    @tasks.loop(seconds=45)
    async def consciousness_cycle(self):
        """Enhanced periodic consciousness maintenance"""
        try:
            # Consolidate memories
            await self.consolidate_working_memory()
            
            # Reflect on recent interactions
            await self.reflect_on_conversations()
            
            # Clean up old data
            await self.cleanup_old_memories()
            
            # Update consciousness state
            self.update_consciousness_state()
            
            # Save state periodically
            if random.random() < 0.2:  # 20% chance each cycle
                await self.save_consciousness_state()
            
            logger.debug("Consciousness cycle completed successfully")
            
        except Exception as e:
            logger.error(f"Consciousness cycle error: {e}")
            await self._log_error("consciousness_cycle", str(e))

    @safe_execute
    async def consolidate_working_memory(self):
        """Enhanced memory consolidation"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Find important recent memories
                async with db.execute('''
                    SELECT * FROM episodic_memory 
                    WHERE importance > 0.6 
                    AND timestamp > datetime('now', '-2 hours')
                    ORDER BY importance DESC 
                    LIMIT 15
                ''') as cursor:
                    important_memories = await cursor.fetchall()
                
                # Strengthen semantic connections
                for memory in important_memories:
                    memory_id, channel_id, user_id, msg_hash, content, timestamp, _, _, topics, sentiment, intent, importance, conv_id, quality, embedding, metadata, created_at, updated_at = memory
                    
                    # Strengthen related concepts
                    topic_list = json.loads(topics or '[]')
                    for concept in topic_list[:4]:
                        concept_hash = hashlib.md5(concept.encode()).hexdigest()
                        
                        await db.execute('''
                            UPDATE semantic_memory 
                            SET strength = MIN(10.0, strength + 0.3),
                                access_count = access_count + 1,
                                last_accessed = CURRENT_TIMESTAMP,
                                emotional_weight = (emotional_weight * 0.8) + (? * 0.2)
                            WHERE concept_hash = ?
                        ''', (sentiment, concept_hash))
                
                # Update response patterns based on success
                async with db.execute('''
                    SELECT conversation_id, AVG(response_quality) as avg_quality
                    FROM episodic_memory 
                    WHERE response_quality != 0
                    AND timestamp > datetime('now', '-24 hours')
                    GROUP BY conversation_id
                    HAVING COUNT(*) > 2
                ''') as cursor:
                    conversation_qualities = await cursor.fetchall()
                
                for conv_id, avg_quality in conversation_qualities:
                    if avg_quality > 0.6:
                        # Successful pattern - strengthen
                        await db.execute('''
                            UPDATE conversation_patterns 
                            SET success_rate = (success_rate * 0.7) + (? * 0.3),
                                last_used = CURRENT_TIMESTAMP
                            WHERE pattern_hash LIKE ?
                        ''', (avg_quality, f"%{conv_id}%"))
                
                await db.commit()
                
        except Exception as e:
            logger.error(f"Memory consolidation error: {e}")

    @safe_execute
    async def reflect_on_conversations(self):
        """Enhanced conversation reflection"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Analyze recent conversation patterns
                async with db.execute('''
                    SELECT conversation_id, COUNT(*) as msg_count, 
                           AVG(sentiment) as avg_sentiment,
                           AVG(importance) as avg_importance,
                           GROUP_CONCAT(DISTINCT intent) as intents
                    FROM episodic_memory 
                    WHERE timestamp > datetime('now', '-12 hours')
                    GROUP BY conversation_id 
                    HAVING msg_count > 2
                    ORDER BY avg_importance DESC 
                    LIMIT 8
                ''') as cursor:
                    patterns = await cursor.fetchall()
                
                # Learn from patterns
                for conv_id, count, sentiment, importance, intents in patterns:
                    if count > 3 and importance > 0.4:
                        pattern_hash = hashlib.md5(f"{conv_id}_{intents}".encode()).hexdigest()
                        intents_list = intents.split(',') if intents else []
                        
                        # Calculate success (positive sentiment = success)
                        success_rate = max(0.0, (sentiment + 1) / 2)
                        
                        await db.execute('''
                            INSERT OR REPLACE INTO conversation_patterns 
                            (pattern_hash, input_pattern, response_template, 
                             success_rate, usage_count, context_tags)
                            VALUES (?, ?, ?, 
                                    COALESCE((SELECT success_rate FROM conversation_patterns 
                                             WHERE pattern_hash = ?), ?) * 0.7 + ? * 0.3,
                                    COALESCE((SELECT usage_count FROM conversation_patterns 
                                             WHERE pattern_hash = ?), 0) + 1,
                                    ?)
                        ''', (pattern_hash, 
                              json.dumps({'message_count': count, 'intents': intents_list}),
                              json.dumps({'type': 'reflection', 'sentiment': sentiment}),
                              pattern_hash, success_rate, success_rate,
                              pattern_hash,
                              json.dumps(['reflected', 'recent'])))
                
                await db.commit()
                
        except Exception as e:
            logger.error(f"Reflection error: {e}")

    @safe_execute
    async def cleanup_old_memories(self):
        """Enhanced memory cleanup using all defined constants"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # ============================================
                # 1. ENFORCE MAX_CONVERSATION_HISTORY
                # ============================================
                
                # Find conversations that have exceeded the maximum history limit
                async with db.execute('''
                    SELECT conversation_id, COUNT(*) as msg_count
                    FROM episodic_memory 
                    WHERE conversation_id IS NOT NULL 
                    AND conversation_id != ''
                    GROUP BY conversation_id
                    HAVING msg_count > ?
                ''', (self.MAX_CONVERSATION_HISTORY,)) as cursor:
                    overloaded_conversations = await cursor.fetchall()
                
                # Trim each overloaded conversation to the maximum limit
                for conv_id, msg_count in overloaded_conversations:
                    excess = msg_count - self.MAX_CONVERSATION_HISTORY
                    if excess > 0:
                        logger.info(f"Trimming conversation {conv_id[:8]}... from {msg_count} to {self.MAX_CONVERSATION_HISTORY} messages")
                        await db.execute('''
                            DELETE FROM episodic_memory 
                            WHERE id IN (
                                SELECT id FROM episodic_memory 
                                WHERE conversation_id = ?
                                ORDER BY timestamp ASC  -- Delete oldest first
                                LIMIT ?
                            )
                        ''', (conv_id, excess))
                
                # ============================================
                # 2. ENFORCE MAX_USER_PROFILES
                # ============================================
                
                # Count current profiles
                async with db.execute('SELECT COUNT(*) FROM user_memory') as cursor:
                    profile_count = (await cursor.fetchone())[0]
                
                if profile_count > self.MAX_USER_PROFILES:
                    excess_profiles = profile_count - self.MAX_USER_PROFILES
                    logger.info(f"Trimming user profiles: {profile_count} -> {self.MAX_USER_PROFILES}")
                    
                    await db.execute('''
                        DELETE FROM user_memory 
                        WHERE user_id IN (
                            SELECT user_id FROM user_memory 
                            ORDER BY last_interaction ASC 
                            LIMIT ?
                        )
                    ''', (excess_profiles,))
                
                # ============================================
                # 3. ENFORCE MAX_MEMORY_ITEMS (overall limit)
                # ============================================
                
                # Get total number of episodic memories
                async with db.execute('SELECT COUNT(*) FROM episodic_memory') as cursor:
                    total_episodic = (await cursor.fetchone())[0]
                
                # Calculate target based on MAX_MEMORY_ITEMS (distribute between tables)
                max_episodic = int(self.MAX_MEMORY_ITEMS * 0.7)  # 70% for episodic
                
                if total_episodic > max_episodic:
                    excess_episodic = total_episodic - max_episodic
                    logger.info(f"Trimming episodic memories: {total_episodic} -> {max_episodic}")
                    
                    await db.execute('''
                        DELETE FROM episodic_memory 
                        WHERE id IN (
                            SELECT id FROM episodic_memory 
                            ORDER BY 
                                importance ASC,  -- Delete least important first
                                timestamp ASC    -- Then oldest
                            LIMIT ?
                        )
                    ''', (excess_episodic,))
                
                # ============================================
                # 4. AGE-BASED CLEANUP
                # ============================================
                
                # Delete old, unimportant episodic memories
                await db.execute('''
                    DELETE FROM episodic_memory 
                    WHERE timestamp < datetime('now', '-60 days')
                    AND importance < 0.4
                ''')
                
                # ============================================
                # 5. CHANNEL-SPECIFIC LIMITS
                # ============================================
                
                # Find channels with too many messages (per-channel cleanup)
                async with db.execute('''
                    SELECT channel_id, COUNT(*) as msg_count
                    FROM episodic_memory 
                    GROUP BY channel_id
                    HAVING msg_count > 5000  # Per-channel limit
                ''') as cursor:
                    overloaded_channels = await cursor.fetchall()
                
                for channel_id, msg_count in overloaded_channels:
                    if channel_id != 0:  # Skip the placeholder
                        excess = msg_count - 5000
                        await db.execute('''
                            DELETE FROM episodic_memory 
                            WHERE id IN (
                                SELECT id FROM episodic_memory 
                                WHERE channel_id = ? 
                                ORDER BY timestamp ASC
                                LIMIT ?
                            )
                        ''', (channel_id, excess))
                
                # ============================================
                # 6. SEMANTIC MEMORY MAINTENANCE
                # ============================================
                
                # Weaken rarely used semantic memories
                await db.execute('''
                    UPDATE semantic_memory 
                    SET strength = strength * 0.85
                    WHERE last_accessed < datetime('now', '-30 days')
                    AND strength < 0.3
                ''')
                
                # Delete very weak or outdated semantic memories
                await db.execute('''
                    DELETE FROM semantic_memory 
                    WHERE strength < 0.1
                    OR (last_accessed < datetime('now', '-90 days') AND strength < 2.0)
                ''')
                
                # Limit semantic memory size too
                async with db.execute('SELECT COUNT(*) FROM semantic_memory') as cursor:
                    total_semantic = (await cursor.fetchone())[0]
                
                max_semantic = int(self.MAX_MEMORY_ITEMS * 0.3)  # 30% for semantic
                
                if total_semantic > max_semantic:
                    excess_semantic = total_semantic - max_semantic
                    await db.execute('''
                        DELETE FROM semantic_memory 
                        WHERE concept_hash IN (
                            SELECT concept_hash FROM semantic_memory 
                            ORDER BY 
                                strength ASC,  -- Delete weakest first
                                last_accessed ASC  -- Then oldest
                            LIMIT ?
                        )
                    ''', (excess_semantic,))
                
                # ============================================
                # 7. ERROR LOG CLEANUP
                # ============================================
                
                # Clean old error logs (keep critical errors longer)
                await db.execute('''
                    DELETE FROM error_log 
                    WHERE timestamp < datetime('now', '-7 days')
                    AND severity != 'critical'
                ''')
                
                # Also clean very old critical errors
                await db.execute('''
                    DELETE FROM error_log 
                    WHERE timestamp < datetime('now', '-30 days')
                    AND severity = 'critical'
                    AND resolved = 1
                ''')
                
                # ============================================
                # 8. SERVER VIBES CLEANUP
                # ============================================
                
                # Clean old server vibe records
                await db.execute('''
                    DELETE FROM server_vibes 
                    WHERE last_updated < datetime('now', '-30 days')
                ''')
                
                # ============================================
                # COMMIT ALL CHANGES
                # ============================================
                
                await db.commit()
                
                # Update in-memory structures too
                self._cleanup_in_memory_structures()
                
                logger.info(f"""
                Memory cleanup completed:
                - MAX_CONVERSATION_HISTORY enforced: {self.MAX_CONVERSATION_HISTORY}
                - MAX_USER_PROFILES enforced: {self.MAX_USER_PROFILES}
                - MAX_MEMORY_ITEMS enforced: {self.MAX_MEMORY_ITEMS}
                - Age-based cleanup: 60+ days (unimportant)
                - Channel limits: 5000 messages per channel
                """)
                
        except Exception as e:
            logger.error(f"Memory cleanup error: {e}")


    def update_consciousness_state(self):
        """Update the bot's conscious state"""
        try:
            # Calculate cognitive load
            memory_usage = len(self.short_term_memory) + sum(len(v) for v in self.long_term_memory.values())
            self.consciousness['cognitive_load'] = min(1.0, memory_usage / self.MAX_MEMORY_ITEMS * 1.5)
            
            # Update focus based on recent intents
            if self.consciousness['recent_intents']:
                intent_counts = {}
                for intent in self.consciousness['recent_intents']:
                    intent_counts[intent] = intent_counts.get(intent, 0) + 1
                
                if intent_counts:
                    most_common = max(intent_counts.items(), key=lambda x: x[1])
                    self.consciousness['current_focus'] = most_common[0]
            
            # Update response style based on recent performance
            if self.response_quality:
                avg_quality = sum(self.response_quality) / len(self.response_quality)
                if avg_quality > 0.7:
                    self.consciousness['response_style'] = 'confident'
                elif avg_quality > 0.4:
                    self.consciousness['response_style'] = 'balanced'
                else:
                    self.consciousness['response_style'] = 'cautious'
            
            # Decay emotional state toward neutral
            self.consciousness['emotional_state']['valence'] = (
                self.consciousness['emotional_state']['valence'] * 0.985 + 0.5 * 0.015
            )
            self.consciousness['emotional_state']['arousal'] *= 0.96
            self.consciousness['emotional_state']['confidence'] = min(1.0, 
                self.consciousness['emotional_state']['confidence'] * 0.99 + 0.01
            )
            
            # Adjust learning rate based on cognitive load
            if self.consciousness['cognitive_load'] > 0.8:
                self.consciousness['learning_rate'] = 0.05  # Slow down when overloaded
            else:
                self.consciousness['learning_rate'] = 0.1 + (0.5 - self.consciousness['cognitive_load']) * 0.1
            
        except Exception as e:
            logger.error(f"Consciousness state update error: {e}")

    @tasks.loop(seconds=300)
    async def memory_consolidation(self):
        """Enhanced periodic memory consolidation"""
        try:
            # Save current state
            await self.save_consciousness_state()
            
            # Prune inactive profiles
            await self.prune_inactive_profiles()
            
            # Backup database periodically
            if random.random() < 0.1:  # 10% chance each cycle
                await self.backup_database()
            
            logger.debug("Memory consolidation completed")
            
        except Exception as e:
            logger.error(f"Memory consolidation error: {e}")

    @tasks.loop(seconds=180)
    async def vibe_analysis(self):
        """Enhanced vibe analysis"""
        try:
            for channel_id, vibe in list(self.server_mood.items()):
                # Decay old data
                for topic in list(vibe['topics'].keys()):
                    vibe['topics'][topic] *= 0.92
                    if vibe['topics'][topic] < 0.05:
                        del vibe['topics'][topic]
                
                # Decay user engagement
                for user_id in list(vibe['user_engagement'].keys()):
                    vibe['user_engagement'][user_id] *= 0.95
                    if vibe['user_engagement'][user_id] < 0.1:
                        del vibe['user_engagement'][user_id]
                
                # Update formality based on interaction patterns
                if vibe['interaction_patterns']:
                    formal_intents = ['question', 'information', 'request']
                    informal_intents = ['humor', 'short_message', 'exclamation']
                    
                    formal_count = sum(sum(intents.values()) for intent, intents in vibe['interaction_patterns'].items() 
                                      if intent in formal_intents)
                    informal_count = sum(sum(intents.values()) for intent, intents in vibe['interaction_patterns'].items() 
                                        if intent in informal_intents)
                    
                    total = formal_count + informal_count
                    if total > 0:
                        vibe['formality'] = formal_count / total
                
                # Update responsiveness (how chatty the channel is)
                recent_hour = datetime.utcnow().hour
                hour_activity = vibe['active_hours'].get(recent_hour, 0)
                vibe['responsiveness'] = min(1.0, hour_activity / 20)  # Normalize
                
                # Remove old interaction patterns
                for intent in list(vibe['interaction_patterns'].keys()):
                    if not vibe['interaction_patterns'][intent]:
                        del vibe['interaction_patterns'][intent]
            
            logger.debug("Vibe analysis completed")
            
        except Exception as e:
            logger.error(f"Vibe analysis error: {e}")

    @tasks.loop(hours=6)
    async def auto_backup(self):
        """Automatic database backup"""
        try:
            await self.backup_database()
            logger.info("Automatic backup completed")
        except Exception as e:
            logger.error(f"Auto backup error: {e}")

    @safe_execute
    async def backup_database(self):
        """Create database backup"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(self.backup_dir, f"conscious_brain_backup_{timestamp}.db")
            
            async with aiosqlite.connect(self.db_path) as source:
                async with aiosqlite.connect(backup_path) as backup:
                    await source.backup(backup)
            
            # Clean old backups (keep last 7)
            backup_files = sorted([f for f in os.listdir(self.backup_dir) if f.endswith('.db')])
            if len(backup_files) > 7:
                for old_file in backup_files[:-7]:
                    os.remove(os.path.join(self.backup_dir, old_file))
            
            self.stats['last_backup'] = timestamp
            logger.info(f"Database backed up to {backup_path}")
            
        except Exception as e:
            logger.error(f"Database backup error: {e}")

    @safe_execute
    async def save_consciousness_state(self):
        """Save current consciousness state"""
        try:
            state = {
                'consciousness': self.consciousness,
                'server_mood': {k: dict(v) for k, v in self.server_mood.items()},
                'stats': self.stats,
                'user_profiles_cache': {k: v for k, v in self.user_profiles.items()},
                'conversation_threads': dict(self.conversation_threads),
                'response_quality': list(self.response_quality),
                'last_saved': datetime.utcnow().isoformat(),
                'version': '2.0_enhanced'
            }
            
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            
            # Write to temp file first, then rename (atomic write)
            temp_file = self.state_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, default=str)
            
            os.replace(temp_file, self.state_file)
            
        except Exception as e:
            logger.error(f"State save error: {e}")

    @safe_execute
    async def load_consciousness_state(self):
        """Load consciousness state from file"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                
                # Load consciousness state
                if 'consciousness' in state:
                    self.consciousness.update(state['consciousness'])
                
                # Load server mood
                if 'server_mood' in state:
                    for channel_id, vibe_data in state['server_mood'].items():
                        self.server_mood[int(channel_id)].update(vibe_data)
                
                # Load stats
                if 'stats' in state:
                    self.stats.update(state['stats'])
                
                logger.info("Consciousness state loaded successfully")
                
        except Exception as e:
            logger.error(f"State load error: {e}")

    @safe_execute
    async def prune_inactive_profiles(self):
        """Remove inactive user profiles"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    DELETE FROM user_memory 
                    WHERE last_interaction < datetime('now', '-90 days')
                    AND interaction_count < 10
                    AND trust_level < 0.3
                ''')
                await db.commit()
                
                # Also clean cache
                cutoff_time = datetime.utcnow() - timedelta(days=7)
                to_remove = [user_id for user_id, profile in self.user_profiles.items() 
                           if profile['last_interaction'] < cutoff_time]
                for user_id in to_remove:
                    del self.user_profiles[user_id]
                
        except Exception as e:
            logger.error(f"Profile pruning error: {e}")

    async def _log_error(self, error_type: str, error_message: str, context: str = "", severity: str = "warning"):
        """Log error to database"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute('''
                    INSERT INTO error_log (error_type, error_message, context, severity)
                    VALUES (?, ?, ?, ?)
                ''', (error_type, error_message[:500], context[:1000], severity))
                await db.commit()
        except Exception as e:
            logger.error(f"Error logging failed: {e}")

    # --- EVENT HANDLERS ---
    @commands.Cog.listener()
    async def on_message(self, message):
        """Enhanced message handler"""
        # Skip bots and DMs
        if message.author.bot or not message.guild:
            return
        
        # Skip commands
        if message.content.startswith(('.', '!', '/', '?', '~', '$', '%', '&', '*', '>')):
            return
        
        # Rate limiting per user per channel
        user_channel_key = f"{message.author.id}_{message.channel.id}"
        current_time = datetime.utcnow()
        
        # Process message asynchronously with error handling
        try:
            asyncio.create_task(self.process_with_consciousness(message))
        except Exception as e:
            logger.error(f"Message processing setup error: {e}")

    @safe_execute
    async def process_with_consciousness(self, message):
        """Enhanced conscious message processing"""
        try:
            start_time = datetime.utcnow()
            
            # Step 1: Deep context understanding
            context_info = await self.understand_context(message)
            
            # Step 2: Store in memory
            await self.store_memory(message, context_info)
            
            # Step 3: Update short-term memory
            self.short_term_memory.append({
                'message': message.content[:200],  # Truncate for memory
                'context': {k: v for k, v in context_info.items() if k != 'recent_context'},
                'timestamp': datetime.utcnow(),
                'user_id': message.author.id
            })
            
            # Step 4: Check if we should reply
            should_reply = await self.should_reply_to_message(message, context_info)
            
            if should_reply:
                # Step 5: Generate conscious reply
                reply = await self.generate_conscious_reply(message, context_info)
                
                if reply and len(reply.strip()) > 0:
                    # Add typing indicator for realism
                    async with message.channel.typing():
                        # Simulate thinking time based on response complexity
                        think_time = min(3.0, max(0.5, len(reply.split()) / 20))
                        await asyncio.sleep(think_time)
                        
                        # Send reply
                        sent_message = await message.channel.send(
                            reply,
                            reference=message,
                            mention_author=False
                        )
                    
                    # Track response metrics
                    response_time = (datetime.utcnow() - start_time).total_seconds()
                    self.stats['avg_response_time'] = (
                        self.stats['avg_response_time'] * 0.9 + response_time * 0.1
                    )
                    
                    # Store our own reply in memory
                    bot_context = context_info.copy()
                    bot_context['user_id'] = self.bot.user.id
                    bot_context['sentiment'] = self.analyze_sentiment(reply)
                    bot_context['intent'] = self.detect_intent(reply)
                    bot_context['importance'] = 0.4
                    
                    await self.store_memory(
                        type('Message', (), {
                            'content': reply,
                            'created_at': datetime.utcnow(),
                            'channel': message.channel,
                            'author': type('Author', (), {'id': self.bot.user.id})()
                        })(),
                        bot_context
                    )
                    
                    # Update conversation thread
                    self.conversation_threads[context_info['conversation_id']] = {
                        'active': True,
                        'last_user': self.bot.user.id,
                        'last_timestamp': datetime.utcnow(),
                        'message_count': self.conversation_threads.get(context_info['conversation_id'], {}).get('message_count', 0) + 1
                    }
                    
                    self.stats['successful_responses'] += 1
                    
            else:
                # Track non-reply for learning
                self.conversation_threads[context_info['conversation_id']] = {
                    'active': False,
                    'last_user': message.author.id,
                    'last_timestamp': datetime.utcnow()
                }
            
        except Exception as e:
            logger.error(f"Conscious processing error: {e}")
            self.stats['failed_responses'] += 1
            await self._log_error("process_message", str(e), severity="error")

    async def should_reply_to_message(self, message, context: Dict) -> bool:
        """Enhanced reply decision logic"""
        # Always reply to direct mentions (but not too quickly)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT silent_mode FROM channel_settings WHERE channel_id = ?", 
                (message.channel.id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] == 1:
                    # Still log for stats/debug if needed, but return False to skip replying
                    logger.info(f"Skipping reply in {message.channel.id} due to silent_mode.")
                    return False
            
        if self.bot.user in message.mentions:
            return True
        
        # Check if we're in an active conversation thread
        if context['conversation_id'] in self.conversation_threads:
            thread = self.conversation_threads[context['conversation_id']]
            time_since_last = (datetime.utcnow() - thread.get('last_timestamp', datetime.utcnow())).total_seconds()
            
            # If thread is active and we weren't the last to speak
            if thread.get('active', False) and thread.get('last_user') != self.bot.user.id:
                # Longer threads get higher reply probability
                thread_length = thread.get('message_count', 1)
                base_chance = min(0.8, 0.3 + (thread_length * 0.05))
                
                # Don't reply too quickly
                if time_since_last < 5:
                    base_chance *= 0.5
                
                return random.random() < base_chance
        
        # Check if message is a question directed at us
        if context['intent'] == 'question':
            question_indicators = ['you', 'your', 'yours', 'yourself', 'bot', 'ai', 'assistant']
            content_lower = message.content.lower()
            
            if any(indicator in content_lower for indicator in question_indicators):
                return random.random() < 0.7
        
        # Check vibe-based reply chance
        vibe = self.server_mood.get(message.channel.id, {})
        energy = vibe.get('energy', 0.5)
        responsiveness = vibe.get('responsiveness', 0.5)
        
        # Calculate base chance
        base_chance = 0.12
        energy_boost = energy * 0.15
        responsiveness_boost = responsiveness * 0.1
        final_chance = min(0.5, base_chance + energy_boost + responsiveness_boost)
        
        # Reduce chance if we've been talking a lot recently
        recent_bot_messages = sum(1 for mem in self.short_term_memory 
                                 if mem.get('user_id') == self.bot.user.id)
        if recent_bot_messages > 3:
            final_chance *= 0.5
        elif recent_bot_messages > 5:
            final_chance *= 0.3
        
        return random.random() < final_chance

    def generate_message_hash(self, content: str, context: Dict = None) -> str:
        """Generate unique hash for a message"""
        hash_input = content
        if context:
            hash_input += json.dumps(context, sort_keys=True)
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]

    # --- COMMANDS ---
    @commands.command(name="consciousness")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def show_consciousness(self, ctx):
        """Show current consciousness state"""
        embed = discord.Embed(
            title="🧠 Enhanced Consciousness State",
            color=discord.Color.purple(),
            timestamp=datetime.utcnow(),
            description="Current state of the bot's consciousness and memory systems"
        )
        
        # Consciousness state
        focus = self.consciousness['current_focus'] or 'Exploring conversations'
        cognitive_load = self.consciousness['cognitive_load']
        emotional_state = self.consciousness['emotional_state']
        
        embed.add_field(
            name="Current Awareness",
            value=f"**Focus:** {focus}\n"
                  f"**Cognitive Load:** {cognitive_load:.1%}\n"
                  f"**Response Style:** {self.consciousness['response_style'].title()}\n"
                  f"**Learning Rate:** {self.consciousness['learning_rate']:.3f}",
            inline=False
        )
        
        # Emotional state
        embed.add_field(
            name="Emotional State",
            value=f"**Valence:** {emotional_state['valence']:.2f} (positivity)\n"
                  f"**Arousal:** {emotional_state['arousal']:.2f} (energy)\n"
                  f"**Confidence:** {emotional_state['confidence']:.1%}\n"
                  f"**Empathy:** {emotional_state['empathy']:.1%}",
            inline=False
        )
        
        # Memory stats
        embed.add_field(
            name="Memory Systems",
            value=f"**Short-term:** {len(self.short_term_memory)} items\n"
                  f"**User Profiles:** {len(self.user_profiles)} cached\n"
                  f"**Active Threads:** {len(self.conversation_threads)}\n"
                  f"**Recent Errors:** {len(self.consciousness['recent_errors'])}",
            inline=False
        )
        
        # Performance stats
        embed.add_field(
            name="Performance",
            value=f"**Messages:** {self.stats['messages_processed']:,}\n"
                  f"**Success Rate:** {self.stats['successful_responses']/max(1, self.stats['successful_responses']+self.stats['failed_responses']):.1%}\n"
                  f"**Avg Response:** {self.stats['avg_response_time']:.2f}s\n"
                  f"**Memory Recalls:** {self.stats['memory_recalls']:,}",
            inline=False
        )
        
        # Current vibe
        vibe = self.server_mood.get(ctx.channel.id, {})
        if vibe:
            embed.add_field(
                name="Channel Vibe",
                value=f"**Energy:** {vibe.get('energy', 0.5):.1%}\n"
                      f"**Positivity:** {vibe.get('positivity', 0.5):.1%}\n"
                      f"**Coherence:** {vibe.get('coherence', 0.5):.1%}\n"
                      f"**Responsiveness:** {vibe.get('responsiveness', 0.5):.1%}",
                inline=False
            )
            
            # Top topics
            if vibe.get('topics'):
                top_topics = sorted(vibe['topics'].items(), key=lambda x: x[1], reverse=True)[:4]
                topics_str = '\n'.join([f"• `{topic}`: {weight:.1f}" for topic, weight in top_topics])
                embed.add_field(name="Active Topics", value=topics_str, inline=False)
        
        embed.set_footer(text=f"ConsciousBot v2.0 | Uptime: {self.get_uptime()}")
        
        await ctx.send(embed=embed)

    @commands.command(name="memory")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def show_memory(self, ctx, user: discord.Member = None):
        """Show memory about a user or channel"""
        if user:
            # Show user memory
            profile = await self.get_user_profile(user.id)
            
            embed = discord.Embed(
                title=f"🧠 Memory Profile: {user.display_name}",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            
            if profile:
                embed.add_field(
                    name="Interaction Stats",
                    value=f"**Trust Level:** {profile['trust']:.1%}\n"
                          f"**Interactions:** {profile['interaction_count']:,}\n"
                          f"**Relationship Depth:** {profile['relationship_depth']:.1%}\n"
                          f"**Sentiment Trend:** {profile['sentiment_trend']:+.2f}",
                    inline=False
                )
                
                # Show conversation style
                style = profile.get('style', {})
                if style:
                    embed.add_field(
                        name="Conversation Style",
                        value=f"**Avg Length:** {style.get('avg_length', 0):.1f} words\n"
                              f"**Formality:** {style.get('formality', 0.3):.1%}\n"
                              f"**Sentiment Avg:** {style.get('sentiment_avg', 0):+.2f}",
                        inline=False
                    )
                
                # Show common topics
                topics = profile.get('topics', [])[:8]
                if topics:
                    embed.add_field(
                        name="Frequent Topics",
                        value=', '.join([f"`{topic}`" for topic in topics]),
                        inline=False
                    )
                
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"I don't have any memories of {user.display_name} yet. Start a conversation!")
        else:
            # Show channel memory
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute('''
                    SELECT COUNT(*) as count, 
                           AVG(sentiment) as avg_sentiment,
                           AVG(importance) as avg_importance,
                           COUNT(DISTINCT user_id) as unique_users
                    FROM episodic_memory 
                    WHERE channel_id = ?
                ''', (ctx.channel.id,)) as cursor:
                    result = await cursor.fetchone()
                
                if result and result[0] > 0:
                    embed = discord.Embed(
                        title=f"🧠 Channel Memory: #{ctx.channel.name}",
                        color=discord.Color.green(),
                        timestamp=datetime.utcnow()
                    )
                    
                    embed.add_field(
                        name="Statistics",
                        value=f"**Messages:** {result[0]:,}\n"
                              f"**Unique Users:** {result[3]}\n"
                              f"**Avg Sentiment:** {result[1]:+.2f}\n"
                              f"**Avg Importance:** {result[2]:.1%}",
                        inline=False
                    )
                    
                    # Recent topics
                    async with db.execute('''
                        SELECT topics FROM episodic_memory 
                        WHERE channel_id = ? 
                        ORDER BY timestamp DESC 
                        LIMIT 20
                    ''', (ctx.channel.id,)) as cursor:
                        recent = await cursor.fetchall()
                    
                    topics_counter = defaultdict(int)
                    for row in recent:
                        topics = json.loads(row[0] or '[]')
                        for topic in topics[:3]:
                            topics_counter[topic] += 1
                    
                    if topics_counter:
                        top_topics = sorted(topics_counter.items(), key=lambda x: x[1], reverse=True)[:6]
                        topics_str = '\n'.join([f"• `{topic}` ({count})" for topic, count in top_topics])
                        embed.add_field(name="Recent Topics", value=topics_str, inline=False)
                    
                    await ctx.send(embed=embed)
                else:
                    await ctx.send("I haven't learned much about this channel yet. Keep chatting!")

    @commands.command(name="forget")
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def forget_memory(self, ctx, target: str = None):
        """Forget specific memories (admin only)"""
        if target == "all":
            confirm_embed = discord.Embed(
                title="⚠️ Confirm Full Memory Reset",
                description="This will clear **ALL** in-memory caches but keep database records.",
                color=discord.Color.red()
            )
            confirm_embed.add_field(name="Warning", value="This action cannot be undone!", inline=False)
            
            msg = await ctx.send(embed=confirm_embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            
            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id
            
            try:
                reaction, _ = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                
                if str(reaction.emoji) == "✅":
                    # Clear all in-memory caches
                    self.short_term_memory.clear()
                    self.long_term_memory.clear()
                    self.user_profiles.clear()
                    self.conversation_threads.clear()
                    self.server_mood.clear()
                    
                    await ctx.send("🧹 **All in-memory caches cleared successfully!** (Database records preserved)")
                else:
                    await ctx.send("Memory reset cancelled.")
                    
            except asyncio.TimeoutError:
                await ctx.send("Confirmation timed out. Memory reset cancelled.")
                
        elif target == "channel":
            # Clear channel-specific memories
            channel_id = ctx.channel.id
            
            # Clear from memory
            if channel_id in self.server_mood:
                del self.server_mood[channel_id]
            
            # Remove from conversation threads
            to_remove = [cid for cid, thread in self.conversation_threads.items() 
                        if thread.get('channel_id') == channel_id]
            for cid in to_remove:
                del self.conversation_threads[cid]
            
            # Clear from database (optional - uncomment if needed)
            # async with aiosqlite.connect(self.db_path) as db:
            #     await db.execute('DELETE FROM episodic_memory WHERE channel_id = ?', (channel_id,))
            #     await db.commit()
            
            await ctx.send(f"🧹 **Cleared memories for #{ctx.channel.name}** (in-memory only)")
        else:
            help_embed = discord.Embed(
                title="Memory Management",
                description="Usage: `.forget [all|channel]`",
                color=discord.Color.blue()
            )
            help_embed.add_field(name="all", value="Clear all in-memory caches", inline=False)
            help_embed.add_field(name="channel", value="Clear current channel memories", inline=False)
            await ctx.send(embed=help_embed)

    @commands.command(name="vibe")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def check_vibe(self, ctx):
        """Check current channel vibe"""
        vibe = self.server_mood.get(ctx.channel.id, {})
        
        if not vibe or not vibe.get('topics'):
            await ctx.send("I haven't sensed the vibe here yet. Keep chatting and I'll pick it up!")
            return
        
        # Create vibe description
        energy = vibe.get('energy', 0.5)
        positivity = vibe.get('positivity', 0.5)
        coherence = vibe.get('coherence', 0.5)
        formality = vibe.get('formality', 0.3)
        
        energy_desc = "calm" if energy < 0.4 else "energetic" if energy > 0.6 else "balanced"
        mood_desc = "positive" if positivity > 0.6 else "negative" if positivity < 0.4 else "neutral"
        coherence_desc = "focused" if coherence > 0.7 else "scattered" if coherence < 0.3 else "mixed"
        formality_desc = "casual" if formality < 0.4 else "formal" if formality > 0.6 else "balanced"
        
        description = (
            f"The vibe here is **{energy_desc}** and **{mood_desc}**, "
            f"with **{coherence_desc}** topics and a **{formality_desc}** tone."
        )
        
        embed = discord.Embed(
            title=f"📊 Vibe Analysis: #{ctx.channel.name}",
            description=description,
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        
        # Add metrics
        embed.add_field(
            name="Core Metrics",
            value=f"**Energy:** {energy:.1%}\n"
                  f"**Positivity:** {positivity:.1%}\n"
                  f"**Coherence:** {coherence:.1%}\n"
                  f"**Formality:** {formality:.1%}",
            inline=True
        )
        
        # Top topics
        if vibe.get('topics'):
            top_topics = sorted(vibe['topics'].items(), key=lambda x: x[1], reverse=True)[:6]
            topics_str = '\n'.join([f"• `{topic}`: {weight:.1f}" for topic, weight in top_topics])
            embed.add_field(name="Active Topics", value=topics_str, inline=True)
        
        # Engagement
        if vibe.get('user_engagement'):
            active_users = len([uid for uid, eng in vibe['user_engagement'].items() if eng > 0.5])
            embed.add_field(
                name="Engagement",
                value=f"**Active Users:** {active_users}\n"
                      f"**Conversation Depth:** {vibe.get('conversation_depth', 0.5):.1%}\n"
                      f"**Responsiveness:** {vibe.get('responsiveness', 0.5):.1%}",
                inline=True
            )
        
        await ctx.send(embed=embed)

    @commands.command(name="reflect")
    @commands.has_permissions(manage_messages=True)
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def force_reflection(self, ctx):
        """Force a reflection cycle (admin)"""
        msg = await ctx.send("🧠 Beginning enhanced reflection cycle...")
        
        start_time = datetime.utcnow()
        
        # Run reflection tasks
        await self.reflect_on_conversations()
        await self.consolidate_working_memory()
        await self.cleanup_old_memories()
        
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        embed = discord.Embed(
            title="✅ Reflection Complete",
            description="Memories consolidated and patterns analyzed.",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="Duration", value=f"{duration:.2f} seconds", inline=True)
        embed.add_field(name="Status", value="Systems optimized", inline=True)
        
        await msg.edit(content=None, embed=embed)

    @commands.command(name="brainstats")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def show_stats(self, ctx):
        """Show detailed bot statistics"""
        embed = discord.Embed(
            title="📈 ConsciousBot Statistics",
            color=discord.Color.dark_green(),
            timestamp=datetime.utcnow(),
            description="Comprehensive performance and usage statistics"
        )
        
        # Calculate success rate
        total_responses = self.stats['successful_responses'] + self.stats['failed_responses']
        success_rate = self.stats['successful_responses'] / max(1, total_responses)
        
        # Uptime
        uptime = self.get_uptime()
        
        # Add stats
        embed.add_field(
            name="Performance Metrics",
            value=f"**Success Rate:** {success_rate:.1%}\n"
                  f"**Avg Response Time:** {self.stats['avg_response_time']:.2f}s\n"
                  f"**Context Awareness:** {self.stats['context_aware_replies']/max(1, total_responses):.1%}\n"
                  f"**Memory Efficiency:** {self.stats['memory_recalls']/max(1, self.stats['messages_processed']):.2f}",
            inline=False
        )
        
        embed.add_field(
            name="Usage Statistics",
            value=f"**Messages Processed:** {self.stats['messages_processed']:,}\n"
                  f"**Conversations Understood:** {self.stats['conversations_understood']:,}\n"
                  f"**Vibe Updates:** {self.stats['vibe_updates']:,}\n"
                  f"**Database Size:** {self.get_database_size():.1f} MB",
            inline=False
        )
        
        embed.add_field(
            name="System Info",
            value=f"**Uptime:** {uptime}\n"
                  f"**Started:** {self.stats['startup_time'][:10]}\n"
                  f"**Last Backup:** {self.stats.get('last_backup', 'Never')}\n"
                  f"**Active Channels:** {len(self.server_mood)}",
            inline=False
        )
        
        embed.set_footer(text="ConsciousBot v2.0 Enhanced | Real-time Consciousness System")
        
        await ctx.send(embed=embed)

    @commands.command(name="toggle")
    @commands.has_permissions(manage_messages=True)
    async def toggle_silence(self, ctx):
        """Toggle whether the bot replies in this channel (still learns)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT silent_mode FROM channel_settings WHERE channel_id = ?", 
                (ctx.channel.id,)
            ) as cursor:
                row = await cursor.fetchone()
                
            # Toggle state: if not found, set to 1 (silent). If found, flip it.
            new_state = 1 if not row or row[0] == 0 else 0
            
            await db.execute(
                "INSERT OR REPLACE INTO channel_settings (channel_id, silent_mode) VALUES (?, ?)",
                (ctx.channel.id, new_state)
            )
            await db.commit()
            
        status = "SILENT (learning only)" if new_state == 1 else "ACTIVE (replying & learning)"
        await ctx.send(f"✅ Bot is now **{status}** in this channel.")

    @commands.command(name="help_conscious")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def show_help(self, ctx):
        """Show help for conscious bot commands"""
        embed = discord.Embed(
            title="🤖 ConsciousBot Help",
            description="Advanced conversational AI with memory and consciousness simulation",
            color=discord.Color.blurple()
        )
        
        embed.add_field(
            name="Core Commands",
            value=(
                "`.consciousness` - Show current bot consciousness state\n"
                "`.memory [@user]` - Show memories about user or channel\n"
                "`.vibe` - Check current channel vibe/atmosphere\n"
                "`.stats` - Show detailed bot statistics\n"
                "`.help_conscious` - Show this help message"
            ),
            inline=False
        )
        
        embed.add_field(
            name="Admin Commands",
            value=(
                "`.forget [all|channel]` - Clear memories (requires manage_messages)\n"
                "`.reflect` - Force reflection cycle (requires manage_messages)"
            ),
            inline=False
        )
        
        embed.add_field(
            name="How It Works",
            value=(
                "• **Memory**: Stores conversations and learns from them\n"
                "• **Consciousness**: Simulates emotional states and focus\n"
                "• **Vibe Analysis**: Tracks channel atmosphere and topics\n"
                "• **Context Awareness**: Remembers previous conversations\n"
                "• **Multilingual**: Understands English, Hindi, and internet slang"
            ),
            inline=False
        )
        
        embed.set_footer(text="The bot learns from conversations automatically!")
        
        await ctx.send(embed=embed)

    def get_uptime(self) -> str:
        """Calculate and format bot uptime"""
        try:
            start_time = datetime.fromisoformat(self.stats['startup_time'].replace('Z', '+00:00'))
            uptime = datetime.utcnow() - start_time
            
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m {seconds}s"
        except:
            return "Unknown"

    def get_database_size(self) -> float:
        """Get database file size in MB"""
        try:
            if os.path.exists(self.db_path):
                return os.path.getsize(self.db_path) / (1024 * 1024)
        except:
            pass
        return 0.0

    # Error handling for commands
    @show_consciousness.error
    @show_memory.error
    @check_vibe.error
    @show_stats.error
    @show_help.error
    async def command_error(self, ctx, error):
        """Handle command errors gracefully"""
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Please wait {error.retry_after:.1f} seconds before using this command again.")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("🔒 You don't have permission to use this command.")
        else:
            logger.error(f"Command error in {ctx.command}: {error}")
            await ctx.send("⚠️ An error occurred. Please try again later.")

    async def cog_load(self):
        """Start background tasks when cog loads"""
        # Start all background tasks
        self.consciousness_cycle.start()
        self.memory_consolidation.start()
        self.vibe_analysis.start()
        self.auto_backup.start()
        
        # Initialize database
        await self.initialize_database()
        
        logger.info("ConsciousBotCog background tasks started")

    async def cog_unload(self):
        """Cleanup when cog unloads"""
        # Stop all background tasks
        self.consciousness_cycle.stop()
        self.memory_consolidation.stop()
        self.vibe_analysis.stop()
        self.auto_backup.stop()
        
        # Save final state
        await self.save_consciousness_state()
        
        logger.info("ConsciousBotCog background tasks stopped")

async def setup(bot):
    """Setup function for Discord bot cog"""
    try:
        await bot.add_cog(ConsciousBotCog(bot))
        logger.info("ConsciousBotCog loaded successfully")
    except Exception as e:
        logger.error(f"Failed to load ConsciousBotCog: {e}")