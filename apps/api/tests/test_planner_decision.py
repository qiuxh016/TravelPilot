from travelpilot_core.agents.planner import local_decision


def test_relaxed_preference_changes_planning_constraints():
    relaxed = local_decision("今天很累，想轻松一点")
    packed = local_decision("行程可以紧凑，多安排景点")
    assert relaxed.pace == "relaxed"
    assert relaxed.constraints()["max_pois_per_day"] < packed.constraints()["max_pois_per_day"]
    assert relaxed.constraints()["buffer_min"] > packed.constraints()["buffer_min"]
