"""Read-only loader for reviewed, repository-owned travel skills."""

import re
from dataclasses import dataclass
from pathlib import Path

_SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
_ALLOWED = {"content-import", "weather-replan"}
_PINNED_VERSIONS = {"content-import": "1.0.0", "weather-replan": "1.0.0"}
_MAX_BYTES = 12_000


@dataclass(frozen=True)
class TravelSkill:
    name: str
    version: str
    description: str
    instructions: str
    applies_to: tuple[str, ...]
    requires: tuple[str, ...]
    excludes: tuple[str, ...]


@dataclass(frozen=True)
class SkillApplicability:
    applicable: bool
    reason_code: str
    missing_requirements: tuple[str, ...] = ()
    matched_exclusions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSelection:
    slug: str | None
    skill: TravelSkill | None
    applicability: SkillApplicability

    def to_record(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "selected": self.skill is not None,
            "skill_name": self.skill.name if self.skill else None,
            "skill_version": self.skill.version if self.skill else None,
            "reason_code": self.applicability.reason_code,
            "missing_requirements": list(self.applicability.missing_requirements),
            "matched_exclusions": list(self.applicability.matched_exclusions),
        }


def available_skills() -> list[dict[str, str]]:
    """Expose only small metadata for routing; instruction bodies load on demand."""
    results = []
    for slug in sorted(_ALLOWED):
        skill = load_skill(slug)
        results.append({
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "applies_to": ",".join(skill.applies_to),
            "requires": ",".join(skill.requires),
            "excludes": ",".join(skill.excludes),
        })
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
    text = content.decode("utf-8").replace("\r\n", "\n")
    match = re.match(r"^---\n(?P<meta>.*?)\n---\n(?P<body>.*)$", text, re.DOTALL)
    if match is None:
        raise ValueError("Skill metadata is malformed")
    metadata = dict(re.findall(
        r"^(name|version|description|applies_to|requires|excludes):\s*(.+)$",
        match.group("meta"), re.MULTILINE,
    ))
    if set(metadata) != {
        "name", "version", "description", "applies_to", "requires", "excludes",
    }:
        raise ValueError("Skill metadata is incomplete")
    if metadata["version"] != _PINNED_VERSIONS[slug]:
        raise ValueError("Skill version is not pinned for this application release")
    return TravelSkill(
        metadata["name"],
        metadata["version"],
        metadata["description"],
        match.group("body").strip(),
        tuple(part.strip() for part in metadata["applies_to"].split(",") if part.strip()),
        tuple(part.strip() for part in metadata["requires"].split(",") if part.strip()),
        tuple(part.strip() for part in metadata["excludes"].split(",") if part.strip()),
    )


def check_skill_applicability(skill: TravelSkill, context: dict[str, object]) -> SkillApplicability:
    task = str(context.get("task", ""))
    if task not in skill.applies_to:
        return SkillApplicability(False, "TASK_MISMATCH")
    excluded = tuple(
        condition for condition in skill.excludes
        if bool(context.get(condition, False))
    )
    if excluded:
        return SkillApplicability(False, "EXCLUDED_CONTEXT", matched_exclusions=excluded)
    missing = tuple(
        requirement for requirement in skill.requires
        if not bool(context.get(requirement, False))
    )
    if missing:
        return SkillApplicability(False, "MISSING_REQUIREMENT", missing_requirements=missing)
    return SkillApplicability(True, "APPLICABLE")


def select_skill(
    task: str,
    text: str = "",
    *,
    context: dict[str, object] | None = None,
) -> TravelSkill | None:
    return select_skill_decision(task, text, context=context).skill


def select_skill_decision(
    task: str,
    text: str = "",
    *,
    context: dict[str, object] | None = None,
) -> SkillSelection:
    """Return both the selected Skill and a safe, structured decision reason."""
    normalized = f"{task} {text}".casefold()
    slug = None
    if task == "content_import" or any(term in normalized for term in ("攻略导入", "导入攻略", "ocr")):
        slug = "content-import"
    elif task == "weather_replan" or any(term in normalized for term in ("下雨", "降雨", "天气变化", "雨天")):
        slug = "weather-replan"
    if slug is None:
        return SkillSelection(None, None, SkillApplicability(False, "NO_MATCH"))
    skill = load_skill(slug)
    applicability = check_skill_applicability(skill, context or {"task": task})
    return SkillSelection(slug, skill if applicability.applicable else None, applicability)
