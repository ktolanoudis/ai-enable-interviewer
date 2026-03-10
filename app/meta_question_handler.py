"""
Meta-Question Handler

Detects and responds to clarifying questions, context checks, and conversational signals
before proceeding with structured interview questions.
"""

import re
from typing import Optional, Tuple

def is_meta_question(text: str) -> bool:
    """
    Detect if user is asking a meta-question about the interview or context.
    
    Args:
        text: User's message
    
    Returns:
        True if this is a meta-question that needs special handling
    """
    text_lower = text.lower().strip()
    
    # Meta-question patterns
    meta_patterns = [
        # Context checks
        r"do you know (what|who|about)",
        r"are you (familiar|aware)",
        r"have you heard of",
        
        # Clarification requests
        r"should i explain",
        r"do you need (me to|more)",
        r"would you like (me to|more)",
        r"how much detail",
        r"how specific",
        
        # Uncertainty signals
        r"i('m| am) not sure (how to|what|if)",
        r"i don('t| do not) know (how to|what|if)",
        r"not sure where to start",
        
        # Interview process questions
        r"what (do you|should i)",
        r"where should i",
        r"how should i",
    ]
    
    for pattern in meta_patterns:
        if re.search(pattern, text_lower):
            return True
    
    return False

def generate_meta_response(user_message: str, current_question_context: str = "") -> Optional[str]:
    """
    Generate appropriate response to meta-questions.
    
    Args:
        user_message: User's meta-question
        current_question_context: What we were asking about (e.g., "your role", "North Star")
    
    Returns:
        Response string, or None if can't handle
    """
    text_lower = user_message.lower().strip()
    
    # "Do you know what [company] does?"
    if re.search(r"do you know (what|who|about)", text_lower):
        return """I don't have specific knowledge about your company yet - this is our first conversation! 

Please feel free to explain in your own words. Even if it seems basic to you, it helps me understand the context of your work.

Go ahead and describe your company and your role however makes sense to you."""
    
    # "Should I explain...?"
    if re.search(r"should i explain", text_lower):
        return """Yes, please explain! Don't assume I know anything about your company or industry. 

The more context you provide, the better I can understand your specific challenges and opportunities."""
    
    # "How much detail...?"
    if re.search(r"how much (detail|specific)", text_lower):
        return """As much detail as feels natural! 

I'm looking for enough context to understand:
- What you actually do day-to-day
- What takes time or feels frustrating
- What tools or systems you use

You can always start high-level and I'll ask follow-up questions if I need more specifics."""
    
    # "I'm not sure / I don't know"
    if re.search(r"(i('m| am) not sure|i don('t| do not) know)", text_lower):
        # Check what they're unsure about
        if "north star" in text_lower or "strategic" in text_lower or "goals" in text_lower:
            return """No problem! If you don't know the high-level strategic goals, that's totally fine.

Let's skip that and focus on what you DO know - your own work.

Tell me about your role: What's your job title, and what are the main things you do in a typical day or week?"""
        else:
            return f"""That's okay! Let's approach this differently.

Instead of {current_question_context}, just tell me in your own words:
- What's your job?
- What do you spend most of your time doing?
- What's frustrating or time-consuming about your work?

There's no wrong answer - I'm just trying to understand your day-to-day."""
    
    # "What do you want to know?" / "What should I say?"
    if re.search(r"what (do you|should i)", text_lower):
        return """Great question! I'm trying to understand:

**About your role:**
- What's your position/job title?
- What department are you in?
- What are your main responsibilities?

**About your work:**
- What tasks do you do regularly?
- What feels slow, repetitive, or frustrating?
- What tools or systems do you use?

Just start with whatever feels easiest to explain, and I'll ask follow-up questions from there."""
    
    return None

def is_correction_signal(text: str) -> bool:
    """
    Detect if user is correcting/disagreeing with something.
    
    Args:
        text: User's message
    
    Returns:
        True if user is saying information is wrong
    """
    text_lower = text.lower().strip()
    
    correction_phrases = [
        "no that's not",
        "no this is not",
        "that's wrong",
        "that's incorrect",
        "not my company",
        "wrong company",
        "that's not right",
        "not correct",
        "different company",
        "not us",
    ]
    
    # Check if starts with "no" and contains negative words
    starts_with_no = text_lower.startswith("no")
    has_negative = any(word in text_lower for word in ["not", "wrong", "incorrect", "different"])
    
    return any(phrase in text_lower for phrase in correction_phrases) or (starts_with_no and has_negative)

def is_uncertainty_signal(text: str) -> bool:
    """
    Detect if user is expressing uncertainty or confusion.
    
    Args:
        text: User's message
    
    Returns:
        True if user seems stuck or confused
    """
    text_lower = text.lower().strip()
    
    uncertainty_phrases = [
        "i have no idea",
        "i don't know",
        "i'm not sure",
        "no clue",
        "not sure",
        "unclear",
        "confused",
        "don't understand",
    ]
    
    return any(phrase in text_lower for phrase in uncertainty_phrases)

def should_skip_question(user_message: str, current_question_type: str) -> Tuple[bool, Optional[str]]:
    """
    Determine if we should skip the current question and adapt.
    
    Args:
        user_message: User's response
        current_question_type: Type of question asked ("north_star", "role", "tasks", etc.)
    
    Returns:
        (should_skip, alternative_question)
    """
    
    if not is_uncertainty_signal(user_message):
        return False, None
    
    # User doesn't know North Star - skip it
    if current_question_type == "north_star":
        return True, """No problem! Many people don't know the high-level strategy.

Let's focus on your own work instead.

**What's your job title and what department are you in?**"""
    
    # User doesn't know their role/department (unlikely but handle it)
    if current_question_type == "role":
        return True, """Okay, let's start even simpler.

**What do you do for work? What are the main things you're responsible for?**"""
    
    return False, None
