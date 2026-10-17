from tests.evaluation.beam_stress_eval import run_stress_evaluation


def test_beam_stress_is_deterministic_and_respects_configured_width():
    first = run_stress_evaluation()
    second = run_stress_evaluation()
    assert first == second
    assert [(item["removed_nodes"], item["candidate_pool"]) for item in first["results"]] == [
        (removed, candidates)
        for removed in (6, 12, 18)
        for candidates in (8, 16, 32)
    ]
    assert all(
        item["peak_frontier_size"] <= 128 * (item["candidate_pool"] + 1)
        for item in first["results"]
    )
    assert first["results"][-1]["states_pruned"] >= first["results"][0]["states_pruned"]
