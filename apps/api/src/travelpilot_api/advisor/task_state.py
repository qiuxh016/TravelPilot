"""Typed, user-visible lifecycle snapshot for the latest Advisor task."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from travelpilot_api.advisor.task_taxonomy import AdvisorTaskType


class AdvisorConfirmationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    answer: str = Field(min_length=1, max_length=200)


class AdvisorTaskSnapshot(BaseModel):
    """Bounded state needed to explain or resume a multi-turn Advisor task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=64)
    task_type: AdvisorTaskType
    status: Literal["WAITING_USER", "PROPOSED", "COMPLETED", "PARTIAL", "CANCELLED", "STALE", "FAILED"]
    goal: str = Field(min_length=1, max_length=1200)
    context_revision: int = Field(ge=0)
    turn_number: int = Field(ge=1, le=100)
    answers: list[str] = Field(default_factory=list, max_length=6)
    question: str | None = Field(default=None, max_length=500)
    confirmation_options: list["AdvisorConfirmationOption"] = Field(default_factory=list, max_length=3)
    action_summary: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if self.status == "WAITING_USER" and not (self.question and self.question.strip()):
            raise ValueError("WAITING_USER tasks require a user question")
        if self.status != "WAITING_USER" and self.question is not None:
            raise ValueError("Only WAITING_USER tasks may have a pending question")
        if self.status == "WAITING_USER" and self.question and not self.confirmation_options:
            self.confirmation_options = confirmation_options_for(self.question)
        if self.turn_number < len(self.answers) + 1:
            raise ValueError("turn_number cannot be behind the recorded answer count")
        return self


def confirmation_options_for(question: str) -> list[AdvisorConfirmationOption]:
    """Turn concrete choices and confirmations into concise, actionable replies."""
    raw_question = question.strip()
    normalized = raw_question.rstrip("？?")
    has_choice_or_confirmation_marker = bool(re.search(
        r"(?:是否|要不要|可不可以|能否|行不行|想不想|需不需要|还是|或者|或|[/／])",
        normalized,
    ))
    if not normalized or not (
        raw_question.endswith(("？", "?"))
        or re.search(r"(?:吗|是否|要不要|可不可以|行不行|想不想|需不需要)$", normalized)
        or has_choice_or_confirmation_marker
    ):
        return []

    asks_for_confirmation = bool(re.search(
        r"(?:需要我|要我|要不要(?:我|把|将)?|要把|是否(?:要|需要|可以)?(?:我|把|将)?|"
        r"(?:可不可以|可以|能否|可否)(?:把|将)?|是否需要我)",
        normalized,
    ))
    proposes_an_action = any(
        verb in normalized
        for verb in ("作为", "加入", "纳入", "添加", "更新", "放到", "排到", "挪到", "设为", "定为")
    )
    if asks_for_confirmation and proposes_an_action:
        date_or_time_proposal = bool(
            re.search(r"\d{1,2}[/月-]\d{1,2}日?", normalized)
            or re.search(r"\d{1,2}:\d{2}", normalized)
            or re.search(r"(?:上午|下午|中午|晚上)?\s*[一二两三四五六七八九十\d]+点", normalized)
            or (
                re.search(r"第[一二两三四五六七八九十\d]+天", normalized)
                and any(verb in normalized for verb in ("放到", "排到", "挪到", "调整到", "安排到"))
            )
            or any(word in normalized for word in ("这一天", "当天", "这个日期", "这天"))
        )
        if date_or_time_proposal:
            return [
                AdvisorConfirmationOption(label="可以，按这个时间继续", answer="可以，按你刚才建议的日期和时间继续安排"),
                AdvisorConfirmationOption(label="换个日期或时间", answer="我想调整刚才建议的日期或时间，先不要按原时间安排"),
                AdvisorConfirmationOption(label="暂不安排", answer="暂不安排这项内容"),
            ]
        return [
            AdvisorConfirmationOption(label="确认，加入安排", answer="确认，按当前选择加入安排"),
            AdvisorConfirmationOption(label="返回调整", answer="先不确认，我想调整这项安排"),
            AdvisorConfirmationOption(label="暂不安排", answer="暂不安排这项内容"),
        ]

    if "还是" in normalized or "或者" in normalized or "或" in normalized or re.search(r"[/／]", normalized):
        choices = re.split(r"还是|或者|或|[/／]", normalized)
        choices = [choice.strip(" ，,、：:；;。？?吗 ") for choice in choices]
        if choices:
            choices[0] = re.sub(
                r"^.*?(?:更倾向于|倾向于|更喜欢|更偏爱|更偏好|偏爱|偏向|更看重|更在意|偏好|更想|想体验|希望去|希望是|希望|想要|想去|选择|选|喜欢)\s*",
                "", choices[0],
            ).strip(" ，,、：:；;。？?吗 ")
            # A common three-way form places the first two options before “还是”.
            if len(choices) == 2:
                choices = re.split(r"[、，,：:]", choices[0]) + [choices[1]]
            choices = [choice for choice in choices if 0 < len(choice) <= 40]
            if 2 <= len(choices) <= 3:
                return [
                    AdvisorConfirmationOption(label=choice, answer=f"我选{choice}")
                    for choice in choices
                ]

    asks_to_continue = any(
        phrase in normalized
        for phrase in ("要我", "需要我", "要不要", "是否要", "是否需要", "想不想", "需不需要")
    )
    proposes_a_next_step = any(verb in normalized for verb in ("排出来", "生成", "推荐", "查询", "按日期安排"))
    if asks_to_continue and proposes_a_next_step:
        return [
            AdvisorConfirmationOption(label="继续，生成待确认建议", answer="好的，继续生成待确认建议"),
            AdvisorConfirmationOption(label="补充或调整要求", answer="我想先补充或调整要求"),
            AdvisorConfirmationOption(label="暂不处理", answer="暂不处理这项任务"),
        ]

    asks_yes_no = bool(re.search(
        r"(?:是否|要不要|可不可以|可以吗|能不能|行不行|好吗|合适吗|合理吗|可行吗|想不想|需不需要|同意吗|确认吗|接受吗|要吗|需要吗|(?:需要我|要我).{0,12}吗)$",
        normalized,
    )) or bool(re.search(r"是否(?:合理|合适|可行|需要|确认|同意|接受)", normalized))
    if asks_yes_no:
        return [
            AdvisorConfirmationOption(label="是，继续", answer="是，请继续"),
            AdvisorConfirmationOption(label="否，先不用", answer="否，暂时不用"),
        ]
    return []


def resolve_task_status(
    *,
    stale: bool = False,
    cancelled: bool = False,
    waiting_for_user: bool = False,
    proposed: bool = False,
    has_partial_results: bool = False,
    tool_failed: bool = False,
    loop_statuses: list[str] | tuple[str, ...] = (),
) -> Literal["WAITING_USER", "PROPOSED", "COMPLETED", "PARTIAL", "CANCELLED", "STALE", "FAILED"]:
    """Map domain-loop outcomes to one consistent user-visible task state."""
    if stale:
        return "STALE"
    if cancelled:
        return "CANCELLED"
    if waiting_for_user:
        return "WAITING_USER"

    failed_statuses = {"FAILED", "REJECTED", "FALLBACK", "STEP_LIMIT"}
    has_failure = tool_failed or any(status in failed_statuses for status in loop_statuses)
    if has_failure:
        return "PARTIAL" if has_partial_results or proposed else "FAILED"
    if proposed:
        return "PROPOSED"
    return "COMPLETED"


def restore_task_snapshot(value: object) -> AdvisorTaskSnapshot | None:
    """Load a session snapshot without allowing malformed context to break reads."""
    if not isinstance(value, dict):
        return None
    try:
        return AdvisorTaskSnapshot.model_validate(value)
    except (TypeError, ValueError):
        return None
