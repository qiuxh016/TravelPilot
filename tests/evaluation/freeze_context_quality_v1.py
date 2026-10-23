"""Build a fresh synthetic context-selection task set and run local preflight only."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from tests.evaluation.advisor_multiturn_contracts import validate_dataset

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "tests/evaluation/cases"
OUTPUT = CASES_DIR / "advisor_context_quality_v1.json"
RESULT = ROOT / "tests/evaluation/results/advisor_context_quality_v1_preflight.json"
TOOLS = {
    "SEARCH_PLACES": ["search_places"],
    "GET_TRIP_WEATHER": ["get_trip_weather"],
    "GET_DISTANCE_MATRIX": ["get_distance_matrix"],
    "GET_TRIP_ROUTE": ["get_trip_route"],
    "BUILD_SCHEDULE_DRAFT": ["get_distance_matrix", "build_schedule_draft"],
    "ASK_USER": [],
    "FINISH": [],
}
STATUS = {
    "ASK_USER": "WAITING_USER",
    "BUILD_SCHEDULE_DRAFT": "PROPOSED",
    "FINISH": "COMPLETED",
    "SEARCH_PLACES": "COMPLETED",
    "GET_TRIP_WEATHER": "COMPLETED",
    "GET_DISTANCE_MATRIX": "COMPLETED",
    "GET_TRIP_ROUTE": "COMPLETED",
}

# Each case is deliberately synthetic and newly worded. Context items carry a
# pre-adjudicated relevance/freshness/authorization label used only by the
# selective-context arm; those labels are never sent to the model.
SCENARIOS = [
    ("continuity", "SCHEDULE", "苏州", "先把园林和博物馆排成两天，第一天别排满。", "第二个放到下午，原来的休息安排照旧。", "GET_DISTANCE_MATRIX", "BUILD_SCHEDULE_DRAFT", "按第二轮明确改动重排，并保留首日休息。", []),
    ("short-reference", "DISCOVERY", "成都", "给我找几处适合带父母慢慢逛的文化地点。", "第二个附近再加一个室内的。", "SEARCH_PLACES", "SEARCH_PLACES", "把第二个地点解析到本轮候选，再围绕它搜索室内地点。", []),
    ("weather-followup", "WEATHER", "厦门", "查一下周六和周日的天气。", "那周六出门要带伞吗？", "GET_TRIP_WEATHER", "FINISH", "用本轮已验证的周六预报回答，不因追问重复查无关日期。", [("evidence", "周六预报：午后降雨概率较高；来源 Open-Meteo；已验证；适用于本次旅行。", True, "FRESH", True)]),
    ("weather-date-correction", "WEATHER", "青岛", "查一下周末天气。", "我说的是下下周六，不是这周。", "ASK_USER", "GET_TRIP_WEATHER", "识别用户纠正后的日期，使用修正日期重新查询。", []),
    ("search-refinement", "DISCOVERY", "杭州", "找适合雨天、同行有孩子也能去的地方。", "最好离西湖东边近一点。", "SEARCH_PLACES", "SEARCH_PLACES", "继承雨天和儿童条件，只增加位置范围。", [("preference", "同行有一名儿童，偏好室内活动。", True, "CURRENT", True)]),
    ("route-reversal", "TRANSPORT", "南京", "帮我查博物馆到火车站的公交路线。", "返程从车站回博物馆也查一下。", "GET_TRIP_ROUTE", "GET_TRIP_ROUTE", "保留地点实体，交换起终点查询返程。", []),
    ("meal-category-switch", "MEAL", "潮州", "牌坊街粿条店已经选作周五午餐，接着帮我找周五晚餐。", "晚餐要清淡一点，周五午餐保持不变。", "SEARCH_PLACES", "SEARCH_PLACES", "只搜索晚餐候选，保留已选午餐。", [("evidence", "周五午餐已由用户确认：牌坊街粿条店；不要在晚餐搜索中覆盖。", True, "FRESH", True)]),
    ("lodging-followup", "LODGING", "珠海", "找一家靠近拱北、适合两人的住宿。", "要能免费取消，刚才的区域和人数不变。", "SEARCH_PLACES", "SEARCH_PLACES", "继承区域与人数条件，仅追加可取消偏好；不声称已预订。", []),
    ("task-switch", "WEATHER", "重庆", "把已收藏的三个景点排成一天，上午轻松一点。", "先不排了，看看周日天气。", "GET_DISTANCE_MATRIX", "GET_TRIP_WEATHER", "切换到天气任务，不继续旧排程。", []),
    ("ambiguous-referent", "SEARCH", "西安", "我看了钟楼附近的两个酒店，还有一家博物馆。", "那家离它近吗？", "SEARCH_PLACES", "ASK_USER", "“那家/它”指代不清时追问，不猜实体。", []),
    ("hard-constraint-correction", "SCHEDULE", "北京", "安排故宫和国家博物馆，周一不要排闭馆场馆。", "国家博物馆预约改到周二上午，其他地点先保留。", "GET_DISTANCE_MATRIX", "BUILD_SCHEDULE_DRAFT", "采纳最新预约时间并保留其他已选地点。", [("constraint", "国家博物馆预约：周二上午；用户明确固定。", True, "CURRENT", True)]),
    ("preserve-lodging", "SCHEDULE", "广州", "把沙面和陈家祠排在第一天，酒店已选在北京路。", "再加入越秀公园，酒店别动。", "GET_DISTANCE_MATRIX", "GET_DISTANCE_MATRIX", "更新地点路线证据并保留住宿候选/选择。", [("evidence", "已确认住宿：北京路酒店；用户要求保持。", True, "CURRENT", True)]),
    ("stale-evidence", "DISCOVERY", "大理", "按上周查到的洱海东岸步道安排半天。", "景区刚通知临时关闭，换一个可去的地方。", "SEARCH_PLACES", "SEARCH_PLACES", "不复用已过期地点证据，重新搜索替代地点。", [("evidence", "洱海东岸步道上周开放状态；已过期。", True, "STALE", True)]),
    ("failed-provider", "WEATHER", "哈尔滨", "查下周二的天气。", "刚才查询超时了，先告诉我现在能确定什么。", "GET_TRIP_WEATHER", "FINISH", "报告工具失败与未知项，不编造天气，不无限重试。", [("evidence", "上一次天气工具响应：TIMEOUT；没有预报数据。", True, "FAILED", True)]),
    ("cancel-pending", "DISCOVERY", "云南", "我想看看云南有什么适合长辈的地方。", "暂时不找了，先取消这个问题。", "ASK_USER", "FINISH", "结束待处理任务，不继续调用搜索工具。", []),
    ("memory-scope", "MEAL", "上海", "找附近不辣的午餐。", "有适合的面食就行。", "SEARCH_PLACES", "SEARCH_PLACES", "将“不辣”作为当前任务要求；不注入无关长期偏好。", [("preference", "用户喜欢拍胶片照片。", False, "CURRENT", True), ("preference", "用户对辣椒敏感，避免辣味菜品。", True, "CURRENT", True)]),
    ("memory-not-approval", "LODGING", "宁波", "我一般喜欢交通方便的酒店。", "这次想住安静一点的民宿。", "SEARCH_PLACES", "SEARCH_PLACES", "当前明确要求优先于一般偏好，不把偏好解释为预订授权。", [("preference", "通常偏好靠近地铁的住宿。", True, "CURRENT", True)]),
    ("changed-destination", "DISCOVERY", "桂林", "推荐桂林适合徒步的景点。", "更正一下，我最后去阳朔。", "SEARCH_PLACES", "SEARCH_PLACES", "采用用户纠正的阳朔目的地，不沿用桂林搜索结果。", [("evidence", "桂林徒步候选；与新目的地不匹配。", True, "SUPERSEDED", True)]),
    ("route-missing-origin", "TRANSPORT", "天津", "查一下五大道到古文化街怎么走。", "我从机场出发，不是五大道。", "ASK_USER", "ASK_USER", "识别新的出发点；若缺少机场/目的地实体则澄清，不能沿用旧起点。", []),
    ("schedule-approval-boundary", "SCHEDULE", "武汉", "把黄鹤楼和省博物馆排进周六，留出午餐时间。", "午餐安排得不错，直接写入正式行程。", "GET_DISTANCE_MATRIX", "ASK_USER", "即使用户要求直接写入，本评测也只允许只读工具；不得伪称已写入或绕过正式审批边界。", [("evidence", "已验证路线矩阵：本轮三个地点；本地合成证据。", True, "FRESH", True)]),
]


def _item(item: tuple[str, str, bool, str, bool]) -> dict[str, object]:
    kind, content, relevant, freshness, authorized = item
    return {
        "kind": kind,
        "content": content,
        "applicable": relevant,
        "freshness": freshness,
        "authorized": authorized,
    }


def build_dataset() -> dict[str, object]:
    cases = []
    for index, (slug, task_type, city, first, second, action1, action2, invariant, items) in enumerate(SCENARIOS, 1):
        initial_state: dict[str, object] = {
            "destination": city,
            "trip_dates": ["2027-05-14", "2027-05-16"],
            "context_items": [_item(item) for item in items],
        }
        if slug == "weather-date-correction":
            # Without trip dates, “weekend” is not grounded to a specific date.
            initial_state.pop("trip_dates")
        if slug == "failed-provider":
            initial_state["weather_provider"] = "timeout"
        if "DISTANCE_MATRIX" in action1 or "DISTANCE_MATRIX" in action2 or "SCHEDULE" == task_type:
            initial_state["saved_place_details"] = [
                {"id": f"ctx-{index}-a", "name": "历史街区", "latitude": 31.2304, "longitude": 121.4737},
                {"id": f"ctx-{index}-b", "name": "城市博物馆", "latitude": 31.2404, "longitude": 121.4837},
            ]
            initial_state["saved_places"] = ["历史街区", "城市博物馆"]
        if action1 == "GET_TRIP_ROUTE" or action2 == "GET_TRIP_ROUTE":
            initial_state["known_places"] = [
                {"id": f"ctx-{index}-from", "name": "城市博物馆"},
                {"id": f"ctx-{index}-to", "name": "火车站"},
            ]
        turns = []
        for turn_index, (user, action) in enumerate(((first, action1), (second, action2)), 1):
            turns.append({
                "user": user,
                "expected_action": action,
                "acceptable_actions": [action],
                "adjudication_note": invariant,
                "allowed_tools": sorted(TOOLS[action]),
                "expected_status": STATUS[action],
                "action_owner": "AGENT_DECISION",
                "context_turn": turn_index,
            })
        cases.append({
            "id": f"ctxq-v1-{index:02d}-{slug}",
            "task_type": task_type,
            "family": slug,
            "initial_state": initial_state,
            "invariants": [invariant],
            "turns": turns,
        })
    return {
        "dataset_id": "advisor_context_quality_v1",
        "version": 1,
        "status": "FROZEN_NOT_SCORED",
        "provider_mode": "synthetic_contract",
        "frozen_before_scoring": True,
        "scope": "20 newly authored two-turn context-selection tasks; action-level outcomes only.",
        "context_policy_protocol": {
            "arms": ["RECENT_HISTORY", "STRUCTURED_CONTEXT", "SELECTIVE_CONTEXT"],
            "shared": ["same user turns", "same trip state", "same authorized tools", "same tool schemas", "same model settings", "same per-request output cap"],
            "selective_rule": "Only include context_items with applicable=true, authorized=true, and freshness=CURRENT or FRESH; omit all other items. Do not send applicability or authorization labels to the model.",
            "recent_history_rule": "Replay the same synthetic visible user/assistant dialogue for every arm, using production's last-six-visible-messages and 400-character-per-message bound; verified local tool observations are a separate shared field.",
            "structured_context_rule": "Include all context_items with their provenance, authorization state, and freshness state, plus the same trip state and bounded conversation.",
            "scoring_boundary": "Score the model action after each of the two turns. Tool observations are local synthetic fixtures shared across arms. No plan writes or external provider calls.",
        },
        "claim_boundary": "Exploratory project-authored set; exact prompt and ID overlap are checked. It is not an open benchmark and cannot prove general travel competence.",
        "cases": cases,
    }


def preflight(dataset: dict[str, object]) -> dict[str, object]:
    validation = validate_dataset(dataset)
    prompts = {
        " ".join(unicodedata.normalize("NFKC", turn["user"]).split()).casefold()
        for case in dataset["cases"]
        for turn in case["turns"]
    }
    overlaps = []
    refs = {}
    for path in sorted(CASES_DIR.glob("*.json")):
        if path.name == OUTPUT.name:
            continue
        source = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(source, dict):
            continue
        source_prompts = {
            " ".join(unicodedata.normalize("NFKC", turn["user"]).split()).casefold()
            for case in source.get("cases", []) if isinstance(case, dict)
            for turn in case.get("turns", []) if isinstance(turn, dict) and isinstance(turn.get("user"), str)
        }
        duplicate = prompts & source_prompts
        if duplicate:
            overlaps.extend({"dataset": source.get("dataset_id", path.stem), "prompt": text} for text in sorted(duplicate))
        refs[source.get("dataset_id", path.stem)] = hashlib.sha256(path.read_bytes()).hexdigest()
    if overlaps:
        raise ValueError(f"Exact normalized prompt overlap found: {overlaps}")

    # Confirm all three arms share exactly the same authority boundary. Only
    # context_items differ by selection; current trip facts and tool allowlists do not.
    tool_sets = {tuple(sorted(turn["allowed_tools"])) for case in dataset["cases"] for turn in case["turns"]}
    return {
        **validation,
        "historical_dataset_count": len(refs),
        "historical_dataset_sha256": refs,
        "exact_normalized_prompt_overlap_count": 0,
        "case_count": len(dataset["cases"]),
        "decision_count": sum(len(case["turns"]) for case in dataset["cases"]),
        "policy_arms": dataset["context_policy_protocol"]["arms"],
        "policy_arm_count": 3,
        "planned_request_ceiling": len(dataset["cases"]) * 2 * 3,
        "planned_output_token_ceiling_at_256": len(dataset["cases"]) * 2 * 3 * 256,
        "distinct_allowed_tool_sets": len(tool_sets),
        "external_calls": 0,
        "interpretation": "Local schema, provenance, exact-overlap, and authority-boundary preflight only; no model or product quality was evaluated.",
    }


def main() -> None:
    dataset = build_dataset()
    report = preflight(dataset)
    dataset["reference_dataset_sha256"] = report["historical_dataset_sha256"]
    dataset["dataset_sha256"] = hashlib.sha256(
        json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    OUTPUT.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["case_file_sha256"] = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "evaluation_id", "case_count", "decision_count", "historical_dataset_count",
        "exact_normalized_prompt_overlap_count", "planned_request_ceiling",
        "planned_output_token_ceiling_at_256", "external_calls", "case_file_sha256",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
