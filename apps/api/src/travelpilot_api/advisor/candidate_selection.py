"""Resolve explicit candidate choices and preserve them across advisor turns."""

import re


def explicit_candidate_selection_ids(content: str, candidates: list) -> list[str]:
    """Resolve a clear natural-language selection to one verified candidate."""
    match = re.search(
        r"(?:我\s*)?(?:选择|选中|选|就用|就选|加入|添加|纳入)\s*([^，,。；;！？!?]{2,80})",
        content.strip(),
    )
    if not match:
        return []
    choice = re.sub(r"[\s（）()\[\]【】]", "", match.group(1)).casefold()
    if len(choice) < 2 or choice in {"这个", "这家", "它", "方案a", "方案b"}:
        return []
    matched: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("id") or not isinstance(candidate.get("name"), str):
            continue
        name = re.sub(r"[\s（）()\[\]【】]", "", candidate["name"]).casefold()
        short_name = re.sub(r"[\s（）()]", "", re.split(r"[（(]", candidate["name"], maxsplit=1)[0]).casefold()
        if choice in name or choice in short_name or name in choice:
            matched.append(str(candidate["id"]))
    return matched if len(matched) == 1 else []


def selected_ids_after_food_search(
    candidates: list,
    explicit_selection_ids: set[str] | list[str] | tuple[str, ...] = (),
    previously_selected_ids: set[str] | list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Keep current stops and every still-selected candidate.

    A follow-up meal search must not silently clear an earlier meal choice. The
    caller's previous selection snapshot already reflects explicit UI removals.
    """
    kept = {
        str(candidate["id"])
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("id") and candidate.get("in_current_plan")
    }
    known_ids = {
        str(candidate["id"])
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("id")
    }
    previously_selected = {str(value) for value in previously_selected_ids}
    explicitly_selected = {str(value) for value in explicit_selection_ids}
    kept.update(previously_selected & known_ids)
    kept.update(explicitly_selected & known_ids)
    return sorted(kept)
