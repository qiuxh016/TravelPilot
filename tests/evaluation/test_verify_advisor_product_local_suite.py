from scripts import verify_advisor_product_local_suite as suite
from tests.evaluation.provenance import EVALUATION_SOURCES


def test_local_product_suite_provenance_pins_harness_and_runtime(monkeypatch):
    monkeypatch.setattr(
        suite,
        "evaluation_provenance",
        lambda _kind: {
            "git_revision": "test-revision",
            "working_tree_dirty": False,
            "source_sha256": {"suite.py": "a" * 64},
        },
    )
    monkeypatch.setattr(
        suite,
        "advisor_runtime_source_manifest",
        lambda _root: {"advisor.py": "b" * 64},
    )
    monkeypatch.setattr(
        suite,
        "fingerprint_manifest",
        lambda manifest: "c" * 64 if manifest == {"advisor.py": "b" * 64} else "wrong",
    )

    result = suite._source_provenance()

    assert result["git_revision"] == "test-revision"
    assert result["working_tree_dirty"] is False
    assert result["source_sha256"] == {"suite.py": "a" * 64}
    assert result["advisor_runtime_source_sha256"] == {"advisor.py": "b" * 64}
    assert result["advisor_runtime_fingerprint"] == "c" * 64
    assert not any("key" in key.casefold() or "token" in key.casefold() for key in result)


def test_local_product_suite_provenance_includes_every_local_provider_entrypoint():
    source_paths = set(EVALUATION_SOURCES["advisor_product_local_suite"])

    assert "scripts/local_openai_chat_stub.py" in source_paths
    assert "tests/evaluation/advisor_product_live_eval.py" in source_paths
    assert "tests/evaluation/local_advisor_weather_app.py" in source_paths
    assert "tests/evaluation/local_advisor_destination_app.py" in source_paths
    assert "tests/evaluation/advisor_route_outage_app.py" in source_paths
