"""Stable identifiers for provider-backed candidates within one Advisor session."""

from collections.abc import Mapping, Sequence
from uuid import UUID, uuid5


def provider_candidate_id(
    *, session_id: UUID, provider: str, provider_id: str, assignment_key: str | None = None,
) -> str:
    """Derive a retry-stable, session-scoped ID for a verified provider place.

    The identifier is not an authorization token. Every operation that accepts
    it must continue to validate the owning user and session.
    """
    normalized_provider = provider.strip().casefold()
    normalized_provider_id = provider_id.strip()
    if not normalized_provider or not normalized_provider_id:
        raise ValueError("A provider and provider place ID are required")
    identity = f"{normalized_provider}:{normalized_provider_id}"
    if assignment_key:
        identity = f"{identity}:assignment:{assignment_key.strip().casefold()}"
    return str(uuid5(session_id, identity))


def candidate_assignment_identity(
    *,
    session_id: UUID,
    provider: str,
    provider_id: str,
    existing_candidates: Sequence[Mapping[str, object]],
    meal_type: str | None = None,
) -> tuple[str, Mapping[str, object] | None]:
    """Resolve a provider place to a stable, independently scheduled assignment."""
    matches = [
        item for item in existing_candidates
        if str(item.get("provider", "")).strip().casefold() == provider.strip().casefold()
        and str(item.get("provider_id", "")).strip() == provider_id.strip()
    ]
    existing = None
    if meal_type:
        existing = next((item for item in matches if item.get("meal_type") == meal_type), None)
        if existing is None:
            existing = next((item for item in matches if not item.get("meal_type")), None)
    elif matches:
        existing = matches[0]

    assignment_key = f"meal:{meal_type}" if meal_type and matches and existing is None else None
    candidate_id = provider_candidate_id(
        session_id=session_id,
        provider=provider,
        provider_id=provider_id,
        assignment_key=assignment_key,
    )
    if existing is None:
        existing = next((item for item in matches if item.get("id") == candidate_id), None)
    return (str(existing["id"]) if existing else candidate_id), existing
