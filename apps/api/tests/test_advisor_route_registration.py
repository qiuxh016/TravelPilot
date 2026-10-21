from travelpilot_api.main import app


def test_advisor_message_endpoint_is_mounted_at_the_public_api_path():
    paths = app.openapi()["paths"]
    message_path = "/api/v1/advisor/sessions/{session_id}/messages"

    assert message_path in paths
    assert "/api/v1/advisor/sessions/{session_id}/selection" in paths
    assert "/api/v1/advisor/advisor/sessions/{session_id}/messages" not in paths
    idempotency_header = next(
        parameter for parameter in paths[message_path]["post"]["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert idempotency_header["required"] is True
