"""Verify advisor ownership and confirm-only persistence against the local database."""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy import delete, func, select
from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.main import app
from travelpilot_api.settings import settings
from travelpilot_core.ports.maps import Place
from travelpilot_infra.db.models.advisor import (
    AdvisorMessageModel,
    AdvisorRunEventModel,
    AdvisorSessionModel,
)
from travelpilot_infra.db.models.plan_change import PlanChangeModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel
from travelpilot_infra.db.session import AsyncSessionFactory
from travelpilot_infra.providers.maps.fake import FakeMapsProvider

API = "/api/v1"
PASSWORD = "travelpilot-test-2026"


async def run():
    suffix = secrets.token_hex(8)
    emails = [f"advisor-{label}-{suffix}@example.invalid" for label in ("a", "b")]
    start_date = datetime.now(UTC).date()
    sessions: dict[str, dict[str, str]] = {}
    previous_llm_settings = settings.llm_api_key, settings.llm_model
    settings.llm_api_key = ""
    settings.llm_model = ""
    class FakeAdvisorMaps(FakeMapsProvider):
        async def search_place(self, keyword, city=None):
            if keyword == "成都东站":
                return [Place("fake", "fake-east-station", "成都东站", "成都市成华区",
                              "TRANSPORT", 30.6550, 104.1370)]
            return await super().search_place(keyword, city)

    app.dependency_overrides[get_maps_provider] = FakeAdvisorMaps

    async def remove_temp_users():
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserModel).where(UserModel.email.in_(emails)))
            await db.commit()

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async def register_and_login(email: str):
                response = await client.post(f"{API}/auth/register", json={
                    "email": email, "password": PASSWORD,
                })
                assert response.status_code == 202, response.text
                mailbox = await client.get(f"{API}/auth/dev-mailbox/{email}")
                assert mailbox.status_code == 200, mailbox.text
                token = parse_qs(urlparse(mailbox.json()["link"]).query)["token"][0]
                response = await client.post(f"{API}/auth/verify-email", json={"token": token})
                assert response.status_code == 200, response.text
                response = await client.post(f"{API}/auth/login", json={
                    "email": email, "password": PASSWORD,
                })
                assert response.status_code == 200, response.text
                return {
                    "session": response.cookies["travelpilot_session"],
                    "csrf": response.cookies["travelpilot_csrf"],
                }

            async def call(method: str, path: str, auth: dict[str, str], **kwargs):
                headers = dict(kwargs.pop("headers", {}))
                headers["Cookie"] = f"travelpilot_session={auth['session']}; travelpilot_csrf={auth['csrf']}"
                if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                    headers["X-CSRF-Token"] = auth["csrf"]
                return await client.request(method, path, headers=headers, **kwargs)

            sessions["a"] = await register_and_login(emails[0])
            sessions["b"] = await register_and_login(emails[1])
            owner_a, owner_b = sessions["a"], sessions["b"]

            created = await call("POST", f"{API}/advisor/sessions", owner_a, json={
                "mode": "destination", "destination": "成都",
                "start_date": start_date.isoformat(),
                "end_date": (start_date + timedelta(days=1)).isoformat(),
                "pace_level": 2, "preference_text": "慢节奏，喜欢街区漫步",
                "origin": "成都东站",
            })
            assert created.status_code == 201, created.text
            advisor_session = created.json()
            session_id = advisor_session["id"]

            assert (await call("GET", f"{API}/advisor/sessions", owner_b)).json() == []
            assert (await call("GET", f"{API}/advisor/sessions/{session_id}", owner_b)).status_code == 404
            assert (await call("POST", f"{API}/advisor/sessions/{session_id}/messages", owner_b,
                               headers={"Idempotency-Key": "foreign-message-01"},
                               json={"content": "越权消息"})).status_code == 404

            message = await call("POST", f"{API}/advisor/sessions/{session_id}/messages", owner_a,
                                 headers={"Idempotency-Key": "advisor-message-replay-01"},
                                 json={"content": "我想去宽窄巷子"})
            assert message.status_code == 200, message.text
            advisor_session = message.json()
            assert any(item["name"] == "宽窄巷子" for item in advisor_session["candidates"])
            replay = await call("POST", f"{API}/advisor/sessions/{session_id}/messages", owner_a,
                                headers={"Idempotency-Key": "advisor-message-replay-01"},
                                json={"content": "我想去宽窄巷子"})
            assert replay.status_code == 200, replay.text
            assert len([item for item in replay.json()["messages"] if item["role"] == "user"]) == 1
            conflict = await call("POST", f"{API}/advisor/sessions/{session_id}/messages", owner_a,
                                  headers={"Idempotency-Key": "advisor-message-replay-01"},
                                  json={"content": "我想去其他地方"})
            assert conflict.status_code == 409, conflict.text
            candidate_id = next(item["id"] for item in advisor_session["candidates"]
                                if item["name"] == "宽窄巷子")

            draft = await call("POST", f"{API}/advisor/sessions/{session_id}/draft", owner_a,
                               json={
                                   "candidate_ids": [candidate_id],
                                   "schedules": [{
                                       "candidate_id": candidate_id,
                                       "day": start_date.isoformat(),
                                       "start_time": "12:00",
                                   }],
                               })
            assert draft.status_code == 200, draft.text
            advisor_session = draft.json()
            assert advisor_session["draft"]["stops"], advisor_session["draft"]
            assert advisor_session["draft"]["transport"]["status"] == "SIMULATED"
            assert advisor_session["draft"]["origin_leg"]["status"] == "SIMULATED"
            assert advisor_session["draft"]["origin_leg"]["from"] == "成都东站"
            assert advisor_session["draft"]["origin_leg"]["duration_min"] > 0
            assert advisor_session["draft"]["stops"][0]["start_time"] > "09:00:00"
            assert advisor_session["status"] == "ACTIVE"
            assert (await call("GET", f"{API}/trips", owner_a)).json() == []

            assert (await call("POST", f"{API}/advisor/sessions/{session_id}/draft", owner_b,
                               json={"candidate_ids": [candidate_id]})).status_code == 404
            assert (await call("POST", f"{API}/advisor/sessions/{session_id}/confirm", owner_b,
                               json={"draft_version": advisor_session["draft_version"]})).status_code == 404
            assert (await call("DELETE", f"{API}/advisor/sessions/{session_id}", owner_b)).status_code == 404

            confirms = await asyncio.gather(*[
                call("POST", f"{API}/advisor/sessions/{session_id}/confirm", owner_a,
                     json={"draft_version": advisor_session["draft_version"]})
                for _ in range(2)
            ])
            assert all(response.status_code == 200 for response in confirms), [
                (response.status_code, response.text) for response in confirms
            ]
            results = [response.json() for response in confirms]
            result = next(item for item in results if not item["already_confirmed"])
            trip_id, plan_id = result["trip_id"], result["plan_id"]
            assert result["plan_version"] == 1
            assert sum(item["already_confirmed"] for item in results) == 1, results
            assert all(item["trip_id"] == trip_id and item["plan_id"] == plan_id for item in results)
            assert (await call("GET", f"{API}/trips/{trip_id}", owner_b)).status_code == 404
            assert (await call("GET", f"{API}/trips/{trip_id}/plans/{plan_id}", owner_b)).status_code == 404
            assert (await call("GET", f"{API}/trips/{trip_id}/wishlist", owner_b)).status_code == 404

            trip_list = await call("GET", f"{API}/trips", owner_a)
            assert len(trip_list.json()) == 1 and trip_list.json()[0]["id"] == trip_id
            wishlist = await call("GET", f"{API}/trips/{trip_id}/wishlist", owner_a)
            plans = await call("GET", f"{API}/trips/{trip_id}/plans", owner_a)
            assert wishlist.status_code == 200 and len(wishlist.json()) == 1, wishlist.text
            assert plans.status_code == 200 and len(plans.json()) == 1, plans.text
            assert plans.json()[0]["id"] == plan_id and len(plans.json()[0]["nodes"]) == 1

            existing_trip_response = await call("POST", f"{API}/trips", owner_a, json={
                "destination": "杭州", "start_date": start_date.isoformat(),
                "end_date": (start_date + timedelta(days=1)).isoformat(), "pace_level": 3,
            })
            assert existing_trip_response.status_code == 201, existing_trip_response.text
            existing_trip_id = existing_trip_response.json()["id"]
            existing_wishlist = await call("POST", f"{API}/trips/{existing_trip_id}/wishlist", owner_a, json={
                "name": "西湖", "category": "ATTRACTION", "latitude": 30.25,
                "longitude": 120.15, "suggested_duration_min": 90,
            })
            assert existing_wishlist.status_code == 201, existing_wishlist.text
            existing_poi_id = existing_wishlist.json()["poi_id"]
            bridge = await call("POST", f"{API}/trips/{existing_trip_id}/wishlist", owner_a, json={
                "name": "断桥", "category": "ATTRACTION", "latitude": 30.26,
                "longitude": 120.16, "suggested_duration_min": 60,
            })
            assert bridge.status_code == 201, bridge.text
            bridge_poi_id = bridge.json()["poi_id"]
            original_plan_response = await call("POST", f"{API}/trips/{existing_trip_id}/plan-runs", owner_a)
            assert original_plan_response.status_code == 201, original_plan_response.text
            original_plan = original_plan_response.json()
            assert original_plan["version"] == 1 and len(original_plan["nodes"]) == 2
            feedback = await call("POST", f"{API}/trips/{existing_trip_id}/feedback", owner_a, json={
                "text": "喜欢轻松散步和湖边风景", "rating": 5,
            })
            assert feedback.status_code == 201, feedback.text
            memory = feedback.json()["candidate_memory"]
            assert memory
            confirmed_memory = await call("PATCH", f"{API}/trips/{existing_trip_id}/memories/{memory['id']}", owner_a,
                                          json={"confirmed": True})
            assert confirmed_memory.status_code == 200, confirmed_memory.text
            existing_session_response = await call("POST", f"{API}/advisor/sessions", owner_a, json={
                "mode": "existing", "trip_id": existing_trip_id, "pace_level": 3,
            })
            assert existing_session_response.status_code == 201, existing_session_response.text
            existing_session = existing_session_response.json()
            assert existing_session["context"]["current_plan"]["version"] == 1
            assert len(existing_session["context"]["confirmed_memories"]) == 1
            west_lake = next(item for item in existing_session["candidates"]
                             if item.get("existing_poi_id") == existing_poi_id)
            broken_bridge = next(item for item in existing_session["candidates"]
                                 if item.get("existing_poi_id") == bridge_poi_id)
            adjustment = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/messages", owner_a,
                                    headers={"Idempotency-Key": f"advisor-adjust-{suffix}"},
                                    json={"content": "不要西湖，保留断桥，节奏轻松一点"})
            assert adjustment.status_code == 200, adjustment.text
            existing_session = adjustment.json()
            assert west_lake["id"] not in existing_session["context"]["selected_candidate_ids"], {
                "removed_candidate": west_lake["id"],
                "removed_candidate_name": west_lake["name"],
                "candidate_names": [item["name"] for item in existing_session["candidates"]],
                "selected_candidate_ids": existing_session["context"]["selected_candidate_ids"],
                "user_message": existing_session["messages"][-2]["content"],
                "applied_adjustments": existing_session["messages"][-1]["payload"].get("adjustments_applied"),
            }
            assert broken_bridge["id"] in existing_session["context"]["selected_candidate_ids"]
            assert existing_session["pace_level"] == 2
            new_place = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/messages", owner_a,
                                   headers={"Idempotency-Key": f"advisor-add-place-{suffix}"},
                                   json={"content": "我还想去宽窄巷子"})
            assert new_place.status_code == 200, new_place.text
            existing_session = new_place.json()
            assert existing_session["pace_level"] == 2, existing_session["context"]
            kuanzhai = next(item for item in existing_session["candidates"] if item["name"] == "宽窄巷子")
            selected_ids = existing_session["context"]["selected_candidate_ids"]
            assert kuanzhai["id"] in selected_ids
            timed_candidates = [
                candidate for candidate in existing_session["candidates"]
                if candidate["id"] in selected_ids
                and candidate.get("planning_category") != "LODGING"
            ]
            existing_draft = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/draft", owner_a,
                                        json={
                                            "candidate_ids": selected_ids,
                                            "schedules": [{
                                                "candidate_id": candidate["id"],
                                                "day": start_date.isoformat(),
                                                "start_time": f"{9 + index * 3:02d}:00",
                                            } for index, candidate in enumerate(timed_candidates)],
                                        })
            assert existing_draft.status_code == 200, existing_draft.text
            existing_draft_data = existing_draft.json()["draft"]
            assert existing_draft_data["transport"]["status"] == "SIMULATED"
            operations = {change["operation"] for change in existing_draft_data["changes"]}
            assert {"ADD", "REMOVE"}.issubset(operations), existing_draft_data["changes"]
            bridge_change = next(change for change in existing_draft_data["changes"]
                                 if change["stable_key"] == bridge_poi_id)
            assert bridge_change["operation"] in {"KEEP", "MOVE"}, bridge_change
            assert existing_draft_data["pace_change"] == {"before": 3, "after": 2}, {
                "pace_change": existing_draft_data["pace_change"],
                "context": existing_session["context"],
                "pace_level": existing_session["pace_level"],
            }
            assert existing_draft.json()["draft_stale"] is False
            unchanged_plan = await call("GET", f"{API}/trips/{existing_trip_id}/plans/{original_plan['id']}", owner_a)
            assert unchanged_plan.status_code == 200 and len(unchanged_plan.json()["nodes"]) == 2
            stale_message = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/messages", owner_a,
                                       headers={"Idempotency-Key": f"advisor-stale-{suffix}"},
                                       json={"content": "不要安排断桥"})
            assert stale_message.status_code == 200 and stale_message.json()["draft_stale"] is True, stale_message.text
            stale_confirm = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/confirm", owner_a,
                                       json={"draft_version": existing_draft.json()["draft_version"]})
            assert stale_confirm.status_code == 409, stale_confirm.text
            existing_session = stale_message.json()
            selected_ids = existing_session["context"]["selected_candidate_ids"]
            timed_candidates = [
                candidate for candidate in existing_session["candidates"]
                if candidate["id"] in selected_ids
                and candidate.get("planning_category") != "LODGING"
            ]
            regenerated = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/draft", owner_a,
                                     json={
                                         "candidate_ids": selected_ids,
                                         "schedules": [{
                                             "candidate_id": candidate["id"],
                                             "day": start_date.isoformat(),
                                             "start_time": f"{9 + index * 3:02d}:00",
                                         } for index, candidate in enumerate(timed_candidates)],
                                     })
            assert regenerated.status_code == 200 and regenerated.json()["draft_stale"] is False, regenerated.text
            existing_confirm = await call("POST", f"{API}/advisor/sessions/{existing_session['id']}/confirm", owner_a,
                                          json={"draft_version": regenerated.json()["draft_version"]})
            assert existing_confirm.status_code == 200, existing_confirm.text
            updated_wishlist = await call("GET", f"/api/v1/trips/{existing_trip_id}/wishlist", owner_a)
            updated_plan = await call("GET", f"/api/v1/trips/{existing_trip_id}/plans/{existing_confirm.json()['plan_id']}", owner_a)
            assert len(updated_wishlist.json()) == 3
            assert any(item["poi_id"] == existing_poi_id for item in updated_wishlist.json())
            assert updated_plan.status_code == 200 and updated_plan.json()["version"] == 2
            assert len(updated_plan.json()["nodes"]) == 1
            assert updated_plan.json()["nodes"][0]["poi_id"] not in {existing_poi_id, bridge_poi_id}
            async with AsyncSessionFactory() as db:
                saved_changes = (await db.scalars(select(PlanChangeModel).where(
                    PlanChangeModel.plan_id == existing_confirm.json()["plan_id"]
                ))).all()
                assert {change.change_type for change in saved_changes} >= {"ADD", "REMOVE"}
            preserved_plan = await call("GET", f"{API}/trips/{existing_trip_id}/plans/{original_plan['id']}", owner_a)
            assert preserved_plan.status_code == 200 and len(preserved_plan.json()["nodes"]) == 2

            exported = await call("GET", f"{API}/account/export", owner_a)
            assert exported.status_code == 200, exported.text
            assert any(item["id"] == session_id for item in exported.json()["advisor_sessions"])
            assert any(item["session_id"] == session_id for item in exported.json()["data"]["advisor_messages"])
            run_events = [item for item in exported.json()["data"]["advisor_run_events"]
                          if item["session_id"] == session_id]
            assert {item["operation"] for item in run_events} >= {"send_message", "create_draft", "confirm_draft"}
            assert any(item["id"] == existing_session["id"] for item in exported.json()["advisor_sessions"])

            deleted = await call("DELETE", f"{API}/account", owner_a)
            assert deleted.status_code == 204, deleted.text
            assert (await call("GET", f"{API}/advisor/sessions", owner_a)).status_code == 401
            async with AsyncSessionFactory() as db:
                assert await db.scalar(select(func.count()).select_from(AdvisorSessionModel).where(
                    AdvisorSessionModel.id == session_id
                )) == 0
                assert await db.scalar(select(func.count()).select_from(AdvisorMessageModel).where(
                    AdvisorMessageModel.session_id == session_id
                )) == 0
                assert await db.scalar(select(func.count()).select_from(AdvisorRunEventModel).where(
                    AdvisorRunEventModel.session_id == session_id
                )) == 0
                assert await db.scalar(select(func.count()).select_from(TripModel).where(
                    TripModel.id == trip_id
                )) == 0

            print("ADVISOR FLOW OK: owner isolation → map verified candidate → confirm only → idempotent confirmation → existing plan and memory context → conversational edits and stale draft protection → new plan version and change log → export → account cascade deletion")
    finally:
        app.dependency_overrides.pop(get_maps_provider, None)
        settings.llm_api_key, settings.llm_model = previous_llm_settings
        await remove_temp_users()


if __name__ == "__main__":
    asyncio.run(run())
