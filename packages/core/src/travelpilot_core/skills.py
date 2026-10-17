"""Read-only loader for reviewed, repository-owned travel skills."""

import re
from dataclasses import dataclass
from pathlib import Path

_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
_ALLOWED = {"content-import", "weather-replan"}
_MAX_BYTES = 12_000


@dataclass(frozen=True)
class TravelSkill:
    name: str
    version: str
    description: str
    instructions: str


def available_skills() -> list[dict[str, str]]:
    """Expose only small metadata for routing; instruction bodies load on demand."""
    results = []
    for slug in sorted(_ALLOWED):
        skill = load_skill(slug)
        results.append({"name": skill.name, "version": skill.version, "description": skill.description})
    return results


def load_skill(slug: str) -> TravelSkill:
    if slug not in _ALLOWED:
        raise ValueError("Unknown travel skill")
    path = (_SKILLS_ROOT / slug / "SKILL.md").resolve()
    if _SKILLS_ROOT.resolve() not in path.parents:
        raise ValueError("Skill path escapes the reviewed repository directory")
    content = path.read_bytes()
    if len(content) > _MAX_BYTES:
        raise ValueError("Skill exceeds the configured size limit")
    text = content.decode("utf-8")
    match = re.match(r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)$", text, re.DOTALL)
    if match is None:
        raise ValueError("Skill metadata is malformed")
    metadata = dict(re.findall(r"^(name|version|description):\s*(.+)$", match.group("meta"), re.MULTILINE))
    if set(metadata) != {"name", "version", "description"}:
        raise ValueError("Skill metadata is incomplete")
    return TravelSkill(metadata["name"], metadata["version"], metadata["description"], match.group("body").strip())


def select_skill(task: str, text: str = "") -> TravelSkill | None:
    normalized = f"{task} {text}".casefold()
    if task == "content_import" or any(term in normalized for term in ("攻略导入", "导入攻略", "ocr")):
        return load_skill("content-import")
    if task == "weather_replan" or any(term in normalized for term in ("下雨", "降雨", "天气变化", "雨天")):
        return load_skill("weather-replan")
    return None
