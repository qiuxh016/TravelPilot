"""Advisor policy checks for traveler hard limits."""

import re

_CLAUSE_BOUNDARY = re.compile(r"[,，;；。.!?！？]|但是|不过|然而|而是|但")
_NEGATIVE_ENDING = re.compile(
    r"(?:不去|不看|不喜欢(?:去|逛|看|参观)?|不想(?:去|逛|看|参观)?|"
    r"不要(?:去|逛|看|参观)?|避开|避免|禁止|不能(?:去|逛|看|参观)?|"
    r"don't\s+like|do\s+not\s+like|don't\s+go\s+to|do\s+not\s+go\s+to|"
    r"avoid|not\s+interested\s+in|never|cannot|can't)\s*(?:的)?$",
    re.IGNORECASE,
)
_POSTFIX_NEGATION = re.compile(
    r"^(?:我|也|都|就|暂时|目前|还是)?\s*(?:不去|不喜欢|不想(?:去)?|"
    r"不要(?:去)?|避开|避免|不能(?:去)?|don't\s+like|do\s+not\s+like)",
    re.IGNORECASE,
)
_AMBIGUOUS_NEGATION = ("不一定", "未必", "不见得", "不能不", "不得不", "不是不", "并非不")


def _excluded_category(memory_text: str, terms: tuple[str, ...]) -> bool:
    """Match a negative constraint to its own clause and category mention."""
    folded = memory_text.casefold()
    for term in terms:
        needle = term.casefold()
        start = 0
        while (position := folded.find(needle, start)) >= 0:
            end = position + len(needle)
            before = folded[:position]
            after = folded[end:]
            left_boundaries = list(_CLAUSE_BOUNDARY.finditer(before))
            clause_start = left_boundaries[-1].end() if left_boundaries else 0
            right_boundary = _CLAUSE_BOUNDARY.search(after)
            clause_end = end + right_boundary.start() if right_boundary else len(folded)
            prefix = folded[clause_start:position].strip()
            suffix = folded[end:clause_end].strip()
            if any(marker in prefix[-24:] for marker in _AMBIGUOUS_NEGATION):
                start = end
                continue
            if _NEGATIVE_ENDING.search(prefix) or _POSTFIX_NEGATION.match(suffix):
                return True
            start = end
    return False


def hard_limit_conflicts(candidate: dict, memories: list[dict]) -> list[str]:
    candidate_text = f"{candidate.get('name', '')} {candidate.get('category', '')}".casefold()
    known_categories = {
        "博物馆": ("博物馆", "museum", "展馆", "art gallery"),
        "公园与自然景点": ("公园", "自然景点", "自然风景", "植物园", "park", "botanical garden"),
        "酒吧与夜店": ("酒吧", "夜店", "bar", "nightclub"),
        "购物": ("购物", "商场", "shopping", "mall"),
        "动物园": ("动物园", "zoo"),
        "宗教场所": ("寺", "教堂", "清真寺", "temple", "church"),
    }
    conflicts = []
    for memory in memories:
        text = str(memory.get("text", ""))
        folded = text.casefold()
        if memory.get("constraint_type") != "HARD_LIMIT":
            continue
        for terms in known_categories.values():
            if (
                any(term.casefold() in candidate_text for term in terms)
                and _excluded_category(folded, terms)
            ):
                conflicts.append(f"{candidate.get('name')} 与已确认限制“{text}”冲突")
                break
    return conflicts


def annotate_profile_memory_conflicts(
    candidates: list[dict], profile_memories: list[dict],
) -> list[dict]:
    """Add read-only warnings so travelers see account-limit conflicts before drafting."""
    annotated = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        annotated.append({
            **candidate,
            "profile_memory_conflicts": hard_limit_conflicts(candidate, profile_memories),
        })
    return annotated


def classify_draft_memory_conflicts(
    candidates: list[dict], profile_memories: list[dict], trip_constraints: list[dict],
) -> tuple[list[str], list[str]]:
    """Keep user-approved profile overrides separate from trip-specific hard limits."""
    profile_conflicts = [
        message for candidate in candidates
        for message in hard_limit_conflicts(candidate, profile_memories)
    ]
    trip_conflicts = [
        message for candidate in candidates
        for message in hard_limit_conflicts(candidate, trip_constraints)
    ]
    return profile_conflicts, trip_conflicts


def overridden_profile_memory_texts(
    candidates: list[dict], profile_memories: list[dict], *, explicitly_overridden: bool,
) -> set[str]:
    """Return only profile limits resolved by the user's one-draft override."""
    if not explicitly_overridden:
        return set()
    return {
        str(memory.get("text", ""))
        for memory in profile_memories
        if any(hard_limit_conflicts(candidate, [memory]) for candidate in candidates)
    }
