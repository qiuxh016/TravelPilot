"""Local OpenAI-compatible response stub for Advisor multi-turn browser tests."""

from __future__ import annotations

import argparse
import ast
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    delay_seconds = 0.0
    agent_decisions = 0
    extraction_requests = 0
    reply_requests = 0
    resumed_context_seen = False
    resumed_decisions = 0
    reference_extraction_seen = False
    reference_history_place_seen = False

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/stats":
            self.send_error(404)
            return
        payload = json.dumps({
            "agent_decisions": type(self).agent_decisions,
            "extraction_requests": type(self).extraction_requests,
            "reply_requests": type(self).reply_requests,
            "resumed_context_seen": type(self).resumed_context_seen,
            "reference_extraction_seen": type(self).reference_extraction_seen,
            "reference_history_place_seen": type(self).reference_history_place_seen,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path == "/reset":
            type(self).agent_decisions = 0
            type(self).extraction_requests = 0
            type(self).reply_requests = 0
            type(self).resumed_context_seen = False
            type(self).resumed_decisions = 0
            type(self).reference_extraction_seen = False
            type(self).reference_history_place_seen = False
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            system = request["messages"][0]["content"]
            user = str(request["messages"][1]["content"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        if "You are a helpful Chinese travel advisor." in str(system):
            type(self).reply_requests += 1
            try:
                facts = json.loads(user)
            except (TypeError, ValueError):
                facts = {}
            history = facts.get("recent_conversation", [])
            current_message = str(facts.get("current_message", ""))
            has_museum_preference = any(
                isinstance(item, dict) and "博物馆" in str(item.get("content", ""))
                for item in history
            )
            if (
                facts.get("memory_suggestions_enabled") is True
                and "我喜欢轻松一点" in current_message
            ):
                content = {
                    "reply": "我会结合你喜欢轻松节奏的偏好来安排。",
                    "follow_up_question": None,
                    "memory_suggestion": {
                        "category": "PACE", "text": "偏好轻松节奏", "evidence": "我喜欢轻松一点",
                    },
                }
            elif "带孩子" in current_message and has_museum_preference:
                content = {
                    "reply": "结合你前面提到的博物馆偏好，可以优先挑选亲子展陈较多的馆；具体活动和开放安排出发前再核对。",
                    "follow_up_question": None,
                }
            elif "西湖" in str(facts.get("current_message", "")) and "灵隐寺" in str(facts.get("current_message", "")):
                content = {
                    "reply": "西湖和灵隐寺都在当前行程中。",
                    "follow_up_question": None,
                }
            elif "结合我这段旅行" in current_message:
                destination = str(facts.get("destination") or "当前目的地")
                places = facts.get("known_trip_places", [])
                names = [
                    str(item.get("name")) for item in places
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                ] if isinstance(places, list) else []
                place_summary = "、".join(names[:3]) or "还没有收藏地点"
                content = {
                    "reply": f"这段{destination}旅行目前收藏了{place_summary}。可以先按地点分布安排每天活动，并预留用餐和休息时间；具体交通与开放信息出发前再核对。",
                    "follow_up_question": None,
                }
            else:
                content = {
                    "reply": "我记下了你提到的旅行偏好，后续建议会结合这段对话和当前旅行信息。",
                    "follow_up_question": None,
                }
        elif "Select exactly one next action using only the supplied observations and tools." in str(system):
            type(self).agent_decisions += 1
            if "discover_destination_from_live_map_evidence" in user:
                try:
                    prompt = json.loads(user)
                    observation = prompt.get("observation", prompt)
                    remaining = observation.get("remaining_search_pairs", [])
                    observed = observation.get("observed_searches", [])
                except (TypeError, ValueError, AttributeError):
                    remaining, observed = [], []
                if not observed and remaining:
                    selected = remaining[0]
                elif observed and remaining:
                    previous_city = observed[0].get("city") if isinstance(observed[0], dict) else None
                    previous_interest = (
                        observed[0].get("interest") if isinstance(observed[0], dict) else None
                    )
                    selected = next((
                        item for item in remaining
                        if isinstance(item, dict)
                        and item.get("city") == previous_city
                        and item.get("interest") != previous_interest
                    ), None)
                else:
                    selected = None
                if selected:
                    content = {
                        "kind": "CALL_TOOL",
                        "tool": "search_destination_places",
                        "arguments": {
                            "city": selected["city"],
                            "interest_index": selected["interest_index"],
                        },
                        "summary": "先查已批准城市的一类地图兴趣，再根据结果补查另一类",
                    }
                else:
                    content = {"kind": "FINISH", "summary": "已完成当前城市的兴趣证据检索"}
            elif "'approved_weather_lookup': True" in user or "'approved_queries': ['室内景点']" in user:
                try:
                    decision_payload = ast.literal_eval(user)
                    observation = decision_payload.get("observation", {})
                except (SyntaxError, ValueError, AttributeError):
                    observation = {}
                weather_seen = any(
                    key in observation for key in ("get_trip_weather", "verified_trip_weather")
                )
                search_seen = "search_places" in observation
                if observation.get("approved_weather_lookup") and not weather_seen:
                    content = {
                        "kind": "CALL_TOOL", "tool": "get_trip_weather",
                        "arguments": {}, "summary": "先核对当前旅行日期的天气",
                    }
                elif "室内景点" in observation.get("approved_queries", []) and not search_seen:
                    content = {
                        "kind": "CALL_TOOL", "tool": "search_places",
                        "arguments": {"keyword": "室内景点"}, "summary": "按雨天需求查找室内备选",
                    }
                else:
                    content = {"kind": "FINISH", "summary": "已检查天气并完成室内候选查询"}
            elif "choose_next_compatible_advisor_stage" in user:
                content = {
                    "kind": "CALL_TOOL", "tool": "run_route_stage",
                    "arguments": {}, "summary": "先完成用户明确要求的路线核验，再整理行程",
                }
            elif "route_between_known_trip_places" in user:
                match = re.search(r"'known_places': \[(.*?)\], 'supported_mode'", user)
                places = re.findall(r"\{'id': '([^']+)', 'name': '([^']+)'\}", match.group(1)) if match else []
                mode_match = re.search(r"'supported_mode': '([^']+)'", user)
                requested_mode = mode_match.group(1) if mode_match else "DRIVING"
                if len(places) >= 2:
                    content = {
                        "kind": "CALL_TOOL", "tool": "get_trip_route",
                        "arguments": {"origin_place_id": places[0][0], "destination_place_id": places[1][0], "mode": requested_mode},
                        "summary": "查询两个已保存地点间的路线",
                    }
                else:
                    content = {"kind": "ASK_USER", "question": "请先保存起点和终点。"}
            elif "'task_type': 'LODGING'" in user:
                match = re.search(r"'approved_queries': \[(.*?)\]", user)
                queries = re.findall(r"'([^']+)'", match.group(1)) if match else []
                if queries:
                    content = {
                        "kind": "CALL_TOOL", "tool": "search_places",
                        "arguments": {"keyword": queries[0]}, "summary": "只查询住宿候选",
                    }
                else:
                    content = {"kind": "FINISH", "summary": "没有剩余的住宿搜索词"}
            elif "'task_type': 'MEAL'" in user:
                match = re.search(r"'approved_queries': \[(.*?)\]", user)
                queries = re.findall(r"'([^']+)'", match.group(1)) if match else []
                if queries:
                    content = {
                        "kind": "CALL_TOOL", "tool": "search_places",
                        "arguments": {"keyword": queries[0]}, "summary": "只查询餐饮候选",
                    }
                else:
                    content = {"kind": "FINISH", "summary": "没有剩余的餐饮搜索词"}
            elif "schedule_existing_trip_places" in user:
                try:
                    decision_payload = ast.literal_eval(user)
                    observation = decision_payload.get("observation", {})
                    loop_observation = observation.get("loop_observation", {})
                except (SyntaxError, ValueError, AttributeError):
                    loop_observation = {}
                if "build_schedule_draft" in loop_observation:
                    content = {"kind": "FINISH", "summary": "待确认草案已完成"}
                elif (
                    "get_distance_matrix_failure" in loop_observation
                    or "get_distance_matrix_verification_failure" in loop_observation
                    or "get_distance_matrix" in loop_observation
                ):
                    content = {
                        "kind": "CALL_TOOL", "tool": "build_schedule_draft",
                        "arguments": {}, "summary": "根据路线观察生成待确认草案",
                    }
                else:
                    content = {
                        "kind": "CALL_TOOL", "tool": "get_distance_matrix",
                        "arguments": {}, "summary": "先核验已选地点间的交通时间",
                    }
            elif "按轻松节奏优化时间安排" in user:
                try:
                    observation = ast.literal_eval(user).get("observation", {})
                except (SyntaxError, ValueError, AttributeError):
                    observation = {}
                if "build_schedule_draft" in observation:
                    content = {"kind": "FINISH", "summary": "轻松节奏草案已完成"}
                elif "get_distance_matrix" in observation:
                    content = {
                        "kind": "CALL_TOOL", "tool": "build_schedule_draft",
                        "arguments": {}, "summary": "根据已核验路线重新排程",
                    }
                else:
                    content = {
                        "kind": "CALL_TOOL", "tool": "get_distance_matrix",
                        "arguments": {}, "summary": "先核验地点交通",
                    }
            elif "原始任务：" in user:
                type(self).resumed_context_seen = all(
                    phrase in user for phrase in ("原始任务：", "先前追问：", "用户补充信息：", "轻松一点")
                )
                type(self).resumed_decisions += 1
                if type(self).resumed_decisions == 1:
                    content = {
                        "kind": "CALL_TOOL", "tool": "build_schedule_draft",
                        "arguments": {}, "summary": "已结合你的回答生成待确认草案",
                    }
                else:
                    content = {"kind": "FINISH", "summary": "草案已完成，可以检查并确认"}
            else:
                type(self).resumed_decisions = 0
                content = {"kind": "ASK_USER", "question": "你希望每天安排得轻松一些吗？"}
        else:
            type(self).extraction_requests += 1
            try:
                extraction_input = json.loads(user)
            except (TypeError, ValueError):
                extraction_input = {"current_message": user, "recent_conversation": []}
            current_message = str(extraction_input.get("current_message", ""))
            history = extraction_input.get("recent_conversation", [])
            if "第一个地点" in current_message and "移除" in current_message:
                type(self).reference_extraction_seen = True
                type(self).reference_history_place_seen = any(
                    isinstance(item, dict) and "西湖" in str(item.get("content", ""))
                    for item in history
                )
            if "第一个地点" in current_message and "移除" in current_message and any(
                isinstance(item, dict) and "西湖" in str(item.get("content", ""))
                for item in history
            ):
                content = {
                    "places": [{
                        "name": "西湖", "category": "ATTRACTION", "confidence": 0.95,
                        "action": "remove", "evidence": "第一个地点",
                    }],
                    "search_queries": [], "intent": "modify", "pace_adjustment": "none",
                }
            elif any(term in current_message for term in (
                "重新给一版", "重新排一版", "重新计算", "重新整理",
                "时间安排", "时间往后挪", "晚些开始", "紧凑一些", "更轻松",
            )):
                pace_adjustment = (
                    "slower" if any(term in current_message for term in (
                        "更轻松", "不要太赶", "慢一点", "轻松一点",
                    )) else "faster" if "紧凑一些" in current_message else "none"
                )
                content = {
                    "places": [], "search_queries": [],
                    "intent": "schedule", "pace_adjustment": pace_adjustment,
                }
            elif "行程" in current_message and any(
                term in current_message for term in ("不要太赶", "多留一些休息时间", "轻松一点", "慢一点")
            ):
                content = {
                    "places": [], "search_queries": [],
                    "intent": "schedule", "pace_adjustment": "slower",
                }
            else:
                content = {
                    "places": [], "search_queries": [],
                    "intent": "general", "pace_adjustment": "none",
                }

        if type(self).delay_seconds > 0:
            time.sleep(type(self).delay_seconds)
        response_body = json.dumps({
            "id": "chatcmpl-local-test",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False)},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
        }, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--delay-seconds", type=float, default=0.0)
    args = parser.parse_args()
    Handler.delay_seconds = max(0.0, args.delay_seconds)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
