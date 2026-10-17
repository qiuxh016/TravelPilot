from travelpilot_api.auth import hash_password, token_digest, verify_password


def test_password_hash_is_salted_and_verified():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)


def test_session_token_digest_is_stable_and_one_way_shaped():
    assert token_digest("opaque-session") == token_digest("opaque-session")
    assert token_digest("opaque-session") != "opaque-session"
