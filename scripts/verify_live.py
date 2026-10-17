"""Run the MVP against the configured PostgreSQL, then remove its test data.

Run from the repository root after `alembic upgrade head`.
"""

import asyncio
import secrets
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
from sqlalchemy import delete
from travelpilot_api.main import app
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.session import AsyncSessionFactory


async def run():
    trip_id = None
    created_pois: set[UUID] = set()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        identity = secrets.token_hex(8)
        auth_response = await client.post("/api/v1/auth/register", json={
            "email": f"verify-{identity}@example.invalid", "password": "travelpilot-test-2026",
        })
        assert auth_response.status_code == 202, auth_response.text
        email = f"verify-{identity}@example.invalid"
        mailbox = await client.get(f"/api/v1/auth/dev-mailbox/{email}")
        verification_token = parse_qs(urlparse(mailbox.json()["link"]).query)["token"][0]
        verified = await client.post("/api/v1/auth/verify-email", json={"token": verification_token})
        assert verified.status_code == 200, verified.text
        logged_in = await client.post("/api/v1/auth/login", json={"email": email, "password": "travelpilot-test-2026"})
        auth = {
            "session": logged_in.cookies["travelpilot_session"],
            "csrf": logged_in.cookies["travelpilot_csrf"],
        }
        async def call(method, path, **kwargs):
            headers = kwargs.setdefault("headers", {})
            headers["Cookie"] = f"travelpilot_session={auth['session']}; travelpilot_csrf={auth['csrf']}"
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                headers["X-CSRF-Token"] = auth["csrf"]
            response = await client.request(method, path, **kwargs)
            if response.is_error:
                raise AssertionError(f"{method} {path}: {response.status_code} {response.text[:500]}")
            return response.json() if response.content else None

        try:
            trip = await call("POST", "/api/v1/trips", json={
                "destination": "成都", "start_date": "2026-10-01", "end_date": "2026-10-04",
                "pace_level": 2, "budget": 6000,
            })
            trip_id = trip["id"]
            content = await call("POST", f"/api/v1/trips/{trip_id}/content", json={
                "content_type": "TEXT", "text": "想去宽窄巷子和熊猫基地，安排轻松一点",
            })
            assert content["status"] == "NEEDS_CONFIRMATION"
            candidate_ids = [entity["candidates"][0]["id"] for entity in content["entities"]
                             if entity["candidates"]]
            assert len(candidate_ids) >= 2
            confirmed = await call("POST", f"/api/v1/content/{content['id']}/confirm",
                                   json={"candidate_ids": candidate_ids})
            assert confirmed["status"] == "IMPORTED"
            wishlist = await call("GET", f"/api/v1/trips/{trip_id}/wishlist")
            assert len(wishlist) >= 2
            created_pois.update(UUID(item["poi_id"]) for item in wishlist)
            # Weather replanning now needs an explicit profile; seed this isolated
            # verification trip rather than relying on category-based assumptions.
            for item in wishlist:
                await call("PATCH", f"/api/v1/wishlist/{item['id']}", json={
                    "environment_profile": {
                        "exposure": "OUTDOOR",
                        "sensitivity": {"RAIN": "HIGH"},
                        "evidence": "Synthetic live-flow verification fixture",
                    },
                })

            first = await call("POST", f"/api/v1/trips/{trip_id}/plan-runs")
            second = await call("POST", f"/api/v1/trips/{trip_id}/plan-runs")
            assert (first["version"], second["version"]) == (1, 2)
            assert await call("GET", f"/api/v1/trips/{trip_id}/plans/{first['id']}") == first

            event = await call("POST", f"/api/v1/trips/{trip_id}/events", json={
                "event_type": "WEATHER_CHANGE", "effective_at": "2026-10-01T09:00:00+08:00",
                "payload": {},
            })
            replanned = await call("POST", f"/api/v1/trips/{trip_id}/replan-runs",
                                   json={"event_id": event["id"]})
            assert replanned["plan"]["version"] == 3
            assert any(change["operation"] == "REMOVE" for change in replanned["diff"])
            assert any("天气敏感地点已移出" in warning
                       for warning in replanned["plan"]["warnings"])
            persisted_diff = await call("GET", f"/api/v1/trips/{trip_id}/plans/{replanned['plan']['id']}/diff")
            assert any(change["operation"] == "REMOVE" and change["reason_code"] == "WEATHER_CHANGE"
                       for change in persisted_diff)
            persisted_changes = await call("GET", f"/api/v1/trips/{trip_id}/plans/{replanned['plan']['id']}/changes")
            assert persisted_changes and all(change["stable_key"] and change["reason_code"]
                                             and change["change_cost"] >= 0 for change in persisted_changes)

            feedback = await call("POST", f"/api/v1/trips/{trip_id}/feedback",
                                  json={"text": "今天很累，希望下午少安排景点", "rating": 2})
            assert feedback["candidate_memory"] is not None
            memory_id = feedback["candidate_memory"]["id"]
            memory = await call("PATCH", f"/api/v1/trips/{trip_id}/memories/{memory_id}",
                                json={"confirmed": True})
            assert memory["confirmed"] is True

            agent = await call("POST", f"/api/v1/trips/{trip_id}/agent-plan-runs",
                               json={"preference": "轻松"})
            assert agent["status"] == "SUCCEEDED"
            assert {tool["tool"] for tool in agent["tool_calls"]} >= {"get_trip_context", "get_distance_matrix"}
            node_id = agent["plan"]["nodes"][0]["id"]
            edited = await call("PATCH", f"/api/v1/trips/{trip_id}/plans/{agent['plan']['id']}/nodes/{node_id}",
                                json={"note": "想早点到达"})
            assert edited["version"] == agent["plan"]["version"] + 1
            assert await call("GET", f"/api/v1/trips/{trip_id}/plans/{agent['plan']['id']}") == agent["plan"]
            edited_node = next(node for node in edited["nodes"] if node["poi_id"] == agent["plan"]["nodes"][0]["poi_id"])
            await call("DELETE", f"/api/v1/trips/{trip_id}/plans/{edited['id']}/nodes/{edited_node['id']}")
            latest = (await call("GET", f"/api/v1/trips/{trip_id}/plans"))[0]
            assert latest["version"] == edited["version"] + 1
            assert len(latest["nodes"]) == len(edited["nodes"]) - 1
            print("LIVE FLOW OK: content → wishlist → plan versions → event → diff → memory → agent → immutable edits")
        finally:
            await call("DELETE", "/api/v1/account")
            if created_pois:
                async with AsyncSessionFactory() as session:
                    await session.execute(delete(PoiModel).where(PoiModel.id.in_(created_pois)))
                    await session.commit()


if __name__ == "__main__":
    asyncio.run(run())
