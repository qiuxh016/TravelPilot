"""Stable identifiers for provider-backed candidates within one Advisor session."""

from uuid import UUID, uuid5


def provider_candidate_id(*, session_id: UUID, provider: str, provider_id: str) -> str:
    """Derive a retry-stable, session-scoped ID for a verified provider place.

    The identifier is not an authorization token. Every operation that accepts
    it must continue to validate the owning user and session.
    """
    normalized_provider = provider.strip().casefold()
    normalized_provider_id = provider_id.strip()
    if not normalized_provider or not normalized_provider_id:
        raise ValueError("A provider and provider place ID are required")
    return str(uuid5(session_id, f"{normalized_provider}:{normalized_provider_id}"))
