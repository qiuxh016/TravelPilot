from travelpilot_api.advisor.stage_execution import AdvisorTurnExecutor
from travelpilot_api.advisor.task_contract import build_agent_task_context
from travelpilot_api.advisor.turn_plan import AdvisorTurnPlan, AdvisorTurnStage
from travelpilot_api.advisor.turn_reporting import build_turn_agent_report


def _stage(name, depends_on=()):
    return AdvisorTurnStage(
        name=name,
        task_context=build_agent_task_context(
            task_type="GENERAL",
            goal="核对行程安排",
            known_facts=(),
            missing_facts=(),
            available_tools=(),
        ),
        depends_on=depends_on,
    )


def test_turn_report_combines_domain_traces_and_surfaces_blocked_work():
    plan = AdvisorTurnPlan(
        primary_task_type="GENERAL",
        stages=(
            _stage("SEARCH_WEATHER"),
            _stage("ROUTE"),
            _stage("SCHEDULE", ("SEARCH_WEATHER",)),
        ),
    )
    executor = AdvisorTurnExecutor(plan)
    executor.statuses.update({"SEARCH_WEATHER": "SUCCEEDED", "ROUTE": "FAILED"})
    executor.traces.update({
        "SEARCH_WEATHER": {
            "status": "SUCCEEDED", "decision_source": "MODEL",
            "decision_summary": "已查询天气", "transitions": [],
        },
        "ROUTE": {
            "status": "FAILED", "decision_source": "RUNTIME_GUARD",
            "decision_summary": "路线服务暂不可用", "transitions": [],
        },
    })
    executor.execution_order.extend(["SEARCH_WEATHER", "ROUTE"])
    executor.recovery = {
        "interrupted_stage": "ROUTE", "completed_stages": ["SEARCH_WEATHER"],
    }

    report = build_turn_agent_report(
        plan=plan,
        executor=executor,
        stage_traces={"SEARCH_WEATHER": None, "ROUTE": None, "SCHEDULE": None},
        orchestration={"order": ["SEARCH_WEATHER", "ROUTE"]},
        supplemental_execution_order=[],
    )

    assert report["executed_stage_order"] == ["SEARCH_WEATHER", "ROUTE"]
    assert report["missing_stage_names"] == ["SCHEDULE"]
    assert report["stage_execution_report"]["stage_statuses"][-1]["status"] == "NOT_EXECUTED"
    assert report["agent_trace"]["status"] == "PARTIAL"
    assert report["agent_trace"]["recovery"]["status"] == "SAME_TASK_SAFE_RETRY"
    assert report["agent_trace"]["turn_orchestration"]["order"] == [
        "SEARCH_WEATHER", "ROUTE",
    ]
