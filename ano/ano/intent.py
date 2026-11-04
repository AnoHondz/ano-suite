"""Bounded spelling repairs for capability selection, never for document content."""
import re


def capability_text(text):
    # Only command verbs, not arbitrary fuzzy matches against user data or names.
    return re.sub(r'\b(?:opene|openn|oppen|opn)\b', 'open', text, flags=re.I)


def command_verb(text):
    # Deterministic execution corrects only the leading imperative. Negation,
    # quotations, filenames and literal bodies are deliberately left untouched.
    return re.sub(r'^(?:opene|openn|oppen|opn)\b', 'open', text, count=1, flags=re.I)


def action_request(text):
    """Conservative status labeling only; never authorizes or chooses an action."""
    return bool(re.match(
        r'^\s*(?:(?:please|can you|could you|would you)\s+)*'
        r'(?:open|opene|openn|oppen|opn|launch|create|make|write|save|add|send|delete|remove|'
        r'rename|edit|revise|install|run|read|find|search|check|list|show|remind)\b', text, re.I))
