"""
Role-based interview strategy classifier

Determines interview approach based on seniority and role context
"""

from enum import Enum

class SeniorityLevel(str, Enum):
    """Employee seniority classification"""
    EXECUTIVE = "executive"          # C-suite, VP, Director
    SENIOR = "senior"                # Senior Manager, Senior roles
    INTERMEDIATE = "intermediate"    # Manager, Analyst, Specialist
    JUNIOR = "junior"                # Associate, Junior roles
    INTERN = "intern"                # Intern, Trainee

def classify_seniority(role: str) -> SeniorityLevel:
    """
    Classify seniority level based on role title
    
    Args:
        role: Job title/position
    
    Returns:
        SeniorityLevel enum
    """
    role_lower = role.lower()
    
    # Executive level
    executive_keywords = [
        'ceo', 'cto', 'cfo', 'coo', 'cio', 'chief', 
        'president', 'vp', 'vice president', 'director', 'head of'
    ]
    if any(keyword in role_lower for keyword in executive_keywords):
        return SeniorityLevel.EXECUTIVE
    
    # Senior level
    senior_keywords = [
        'senior manager', 'senior', 'sr.', 'sr ', 'lead', 'principal'
    ]
    if any(keyword in role_lower for keyword in senior_keywords):
        return SeniorityLevel.SENIOR
    
    # Intermediate level
    intermediate_keywords = [
        'manager', 'analyst', 'specialist', 'coordinator', 'consultant',
        'engineer', 'developer', 'designer', 'accountant'
    ]
    if any(keyword in role_lower for keyword in intermediate_keywords):
        return SeniorityLevel.INTERMEDIATE
    
    # Intern level
    intern_keywords = ['intern', 'trainee', 'apprentice', 'student']
    if any(keyword in role_lower for keyword in intern_keywords):
        return SeniorityLevel.INTERN
    
    # Junior level (default for associates, assistants, etc.)
    return SeniorityLevel.JUNIOR

def should_ask_north_star(seniority: SeniorityLevel, has_existing_north_star: bool) -> bool:
    """
    Determine if North Star question should be asked
    
    Args:
        seniority: Employee seniority level
        has_existing_north_star: Whether company already has North Star defined
    
    Returns:
        True if should ask about North Star
    """
    # Always skip if we already have North Star from previous interviews
    if has_existing_north_star:
        return False
    
    # Ask executives and senior people
    return seniority in [SeniorityLevel.EXECUTIVE, SeniorityLevel.SENIOR]
