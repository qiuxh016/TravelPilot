"""Exercise account registration, owner isolation, export, and cascade deletion."""
import asyncio
import secrets
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
from PIL import Image
from sqlalchemy import func, select
from travelpilot_api.dependencies import get_maps_provider
from travelpilot_api.main import app
from travelpilot_api.settings import settings
from travelpilot_infra.db.models.plan import TripPlanModel
from travelpilot_infra.db.models.poi import PoiModel
from travelpilot_infra.db.models.social import MomentMediaModel, TravelMomentModel
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.models.user import UserModel, UserSessionModel
from travelpilot_infra.db.models.wishlist import WishlistItemModel
from travelpilot_infra.db.models.workflow import UserMemorySettingsModel, UserPreferenceMemoryModel
from travelpilot_infra.db.session import AsyncSessionFactory
from travelpilot_infra.providers.maps.fake import FakeMapsProvider

API = "/api/v1"
PASSWORD = "travelpilot-test-2026"

# Keep this verification deterministic and prevent accidental paid LLM or map calls.
settings.llm_api_key = ""
settings.llm_model = ""
app.dependency_overrides[get_maps_provider] = FakeMapsProvider


async def run():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        suffix = secrets.token_hex(8)
        users = []
        async def register(label):
            response = await client.post(f"{API}/auth/register", json={
                "email": f"{label}-{suffix}@example.invalid", "password": PASSWORD,
            })
            assert response.status_code == 202, response.text
            email = f"{label}-{suffix}@example.invalid"
            mailbox = await client.get(f"{API}/auth/dev-mailbox/{email}")
            verification_token = parse_qs(urlparse(mailbox.json()["link"]).query)["token"][0]
            verified = await client.post(f"{API}/auth/verify-email", json={"token": verification_token})
            assert verified.status_code == 200, verified.text
            login = await client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
            result = login.json()
            auth = {
                "session": login.cookies["travelpilot_session"],
                "csrf": login.cookies["travelpilot_csrf"],
            }
            users.append((result["user"]["id"], auth))
            return result

        async def request(method, path, auth, **kwargs):
            headers = kwargs.pop("headers", {})
            headers["Cookie"] = f"travelpilot_session={auth['session']}; travelpilot_csrf={auth['csrf']}"
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                headers["X-CSRF-Token"] = auth["csrf"]
            return await client.request(method, path, headers=headers, **kwargs)

        await register("owner-a")
        await register("owner-b")
        client.cookies.clear()
        assert (await client.get(f"{API}/trips")).status_code == 401
        owner_id, token_a = users[0]
        _, token_b = users[1]
        enabled = await request("PUT", f"{API}/account/memory-settings", token_a,
                                json={"enabled": True})
        assert enabled.status_code == 200 and enabled.json()["enabled"]
        saved_memory = await request("POST", f"{API}/account/memories", token_a, json={
            "category": "PACE", "text": "偏好轻松节奏", "constraint_type": "PREFERENCE",
        })
        assert saved_memory.status_code == 201, saved_memory.text
        memory_id = saved_memory.json()["id"]
        assert (await request("GET", f"{API}/account/memories", token_b)).json() == []
        assert (await request("PATCH", f"{API}/account/memories/{memory_id}", token_b,
                              json={"text": "越权修改"})).status_code == 404
        assert (await request("DELETE", f"{API}/account/memories/{memory_id}", token_b)).status_code == 404
        duplicate = await client.post(f"{API}/auth/register", json={
            "email": f"OWNER-B-{suffix}@EXAMPLE.INVALID", "password": PASSWORD,
        })
        assert duplicate.status_code == 409
        for index in range(4):
            failed = await client.post(f"{API}/auth/login", json={
                "email": f"limited-{suffix}@example.invalid", "password": f"wrong-password-{index}",
            })
            assert failed.status_code == 401, failed.text
        locked = await client.post(f"{API}/auth/login", json={
            "email": f"limited-{suffix}@example.invalid", "password": "wrong-password-final",
        })
        assert locked.status_code == 429, locked.text
        relogin = await client.post(f"{API}/auth/login", json={
            "email": f"OWNER-B-{suffix}@EXAMPLE.INVALID", "password": PASSWORD,
        })
        assert relogin.status_code == 200, relogin.text
        token_b = {
            "session": relogin.cookies["travelpilot_session"],
            "csrf": relogin.cookies["travelpilot_csrf"],
        }
        reset_password = "travelpilot-test-reset-2026"
        await client.post(f"{API}/auth/password-reset/request", json={"email": f"owner-b-{suffix}@example.invalid"})
        reset_mail = await client.get(f"{API}/auth/dev-mailbox/owner-b-{suffix}@example.invalid")
        reset_token = parse_qs(urlparse(reset_mail.json()["link"]).query)["token"][0]
        reset = await client.post(f"{API}/auth/password-reset/confirm", json={
            "token": reset_token, "password": reset_password,
        })
        assert reset.status_code == 200, reset.text
        assert (await client.post(f"{API}/auth/password-reset/confirm", json={
            "token": reset_token, "password": reset_password,
        })).status_code == 400
        assert (await request("GET", f"{API}/auth/me", token_b)).status_code == 401
        relogin_reset = await client.post(f"{API}/auth/login", json={
            "email": f"owner-b-{suffix}@example.invalid", "password": reset_password,
        })
        assert relogin_reset.status_code == 200, relogin_reset.text
        token_b = {
            "session": relogin_reset.cookies["travelpilot_session"],
            "csrf": relogin_reset.cookies["travelpilot_csrf"],
        }

        async def create_trip(token, name):
            response = await request("POST", f"{API}/trips", token, json={
                "destination": name, "start_date": "2026-11-01", "end_date": "2026-11-03",
                "pace_level": 2,
            })
            assert response.status_code == 201, response.text
            return response.json()

        trip_a = await create_trip(token_a, "成都 A")
        trip_b = await create_trip(token_b, "杭州 B")
        advisor = await request("POST", f"{API}/advisor/sessions", token_a, json={
            "mode": "discover", "preference_text": "想找自然风景",
        })
        assert advisor.status_code == 201, advisor.text
        assert advisor.json()["context"]["personal_memories"][0]["text"] == "偏好轻松节奏"
        assert (await request("GET", f"{API}/advisor/sessions/{advisor.json()['id']}", token_b)).status_code == 404
        list_b = await request("GET", f"{API}/trips", token_b)
        assert [row["id"] for row in list_b.json()] == [trip_b["id"]]
        wishlist = await request("POST", f"{API}/trips/{trip_a['id']}/wishlist", token_a, json={
            "name": "宽窄巷子", "category": "ATTRACTION", "latitude": 30.66,
            "longitude": 104.06, "suggested_duration_min": 90,
        })
        assert wishlist.status_code == 201, wishlist.text
        item_id = wishlist.json()["id"]
        poi_id = wishlist.json()["poi_id"]
        plan = await request("POST", f"{API}/trips/{trip_a['id']}/plan-runs", token_a)
        assert plan.status_code == 201, plan.text
        plan_id = plan.json()["id"]
        imported = await request("POST", f"{API}/trips/{trip_a['id']}/content", token_a,
                                 json={"content_type": "TEXT", "text": "想去宽窄巷子"})
        assert imported.status_code == 201, imported.text
        assert imported.json()["skill_applicability"] == {
            "slug": "content-import", "selected": True, "skill_name": "攻略导入",
            "skill_version": "1.0.0", "reason_code": "APPLICABLE",
            "missing_requirements": [], "matched_exclusions": [],
        }
        content_id = imported.json()["id"]
        imported_candidate_ids = [candidate["id"] for entity in imported.json()["entities"]
                                  for candidate in entity["candidates"]]
        assert imported_candidate_ids
        rejected_import = await request("POST", f"{API}/trips/{trip_a['id']}/content", token_a,
                                        json={"content_type": "TEXT", "text": "  "})
        assert rejected_import.status_code == 201, rejected_import.text
        assert rejected_import.json()["skill_applicability"] == {
            "slug": "content-import", "selected": False, "skill_name": None,
            "skill_version": None, "reason_code": "MISSING_REQUIREMENT",
            "missing_requirements": ["user_content"], "matched_exclusions": [],
        }
        event = await request("POST", f"{API}/trips/{trip_a['id']}/events", token_a, json={
            "event_type": "POI_CANCELLED", "effective_at": "2026-11-01T09:00:00+08:00",
            "payload": {"poi_id": str(poi_id)},
        })
        assert event.status_code == 201, event.text
        event_id = event.json()["id"]

        friend_email = f"owner-b-{suffix}@example.invalid"
        requested = await request("POST", f"{API}/friends/requests", token_a, json={"email": friend_email})
        assert requested.status_code == 202, requested.text
        incoming = await request("GET", f"{API}/friends/requests", token_b)
        assert incoming.status_code == 200 and len(incoming.json()) == 1, incoming.text
        friendship_id = incoming.json()[0]["id"]
        accepted = await request("POST", f"{API}/friends/requests/{friendship_id}/accept", token_b)
        assert accepted.status_code == 200 and accepted.json()["status"] == "ACCEPTED", accepted.text

        private_moment = await request("POST", f"{API}/moments", token_a, json={
            "body": "这是我的私密旅行记录", "trip_id": trip_a["id"], "audience": "PRIVATE",
        })
        assert private_moment.status_code == 201, private_moment.text
        friend_moment = await request("POST", f"{API}/moments", token_a, json={
            "body": "和好友分享的旅行感受", "trip_id": trip_a["id"], "place_name": "宽窄巷子",
            "audience": "FRIENDS",
        })
        assert friend_moment.status_code == 201, friend_moment.text
        moment_id = friend_moment.json()["id"]
        private_moment_id = private_moment.json()["id"]
        jpeg = BytesIO()
        source_image = Image.new("RGB", (64, 48), (22, 120, 64))
        exif = Image.Exif()
        exif[0x010E] = "sensitive location metadata"
        source_image.save(jpeg, format="JPEG", exif=exif)
        uploaded = await request("POST", f"{API}/moments/{moment_id}/media", token_a,
                                 content=jpeg.getvalue(), headers={"Content-Type": "image/jpeg"})
        assert uploaded.status_code == 201, uploaded.text
        asset_id = uploaded.json()["id"]
        private_upload = await request("POST", f"{API}/moments/{private_moment_id}/media", token_a,
                                       content=jpeg.getvalue(), headers={"Content-Type": "image/jpeg"})
        assert private_upload.status_code == 201, private_upload.text
        private_asset_id = private_upload.json()["id"]
        feed_b = await request("GET", f"{API}/moments", token_b)
        feed_ids = {row["id"] for row in feed_b.json()}
        assert moment_id in feed_ids and private_moment_id not in feed_ids
        shared_image = await request("GET", f"{API}/media/{asset_id}", token_b)
        assert shared_image.status_code == 200, shared_image.text
        decoded = Image.open(BytesIO(shared_image.content))
        assert len(decoded.getexif()) == 0, "image metadata was not stripped"
        assert (await request("GET", f"{API}/media/{private_asset_id}", token_b)).status_code == 404
        assert (await request("PATCH", f"{API}/moments/{moment_id}", token_b,
                              json={"body": "越权修改"})).status_code == 404
        assert (await request("DELETE", f"{API}/moments/{moment_id}", token_b)).status_code == 404
        comment = await request("POST", f"{API}/moments/{moment_id}/comments", token_b,
                                json={"body": "祝旅途愉快"})
        assert comment.status_code == 201, comment.text
        assert (await request("PUT", f"{API}/moments/{moment_id}/reaction", token_b)).status_code == 204
        feed_a = await request("GET", f"{API}/moments", token_a)
        friend_post = next(row for row in feed_a.json() if row["id"] == moment_id)
        assert friend_post["comment_count"] == 1 and friend_post["reaction_count"] == 1
        private_response = await request("PATCH", f"{API}/moments/{moment_id}", token_a,
                                         json={"audience": "PRIVATE"})
        assert private_response.status_code == 200, private_response.text
        assert moment_id not in {row["id"] for row in (await request("GET", f"{API}/moments", token_b)).json()}
        assert (await request("GET", f"{API}/media/{asset_id}", token_b)).status_code == 404
        await request("PATCH", f"{API}/moments/{moment_id}", token_a, json={"audience": "FRIENDS"})

        summary_share = await request("POST", f"{API}/trips/{trip_a['id']}/shares", token_a, json={
            "friend_user_id": users[1][0], "scope": "TRIP_SUMMARY",
        })
        assert summary_share.status_code == 201, summary_share.text
        shared_summary = await request("GET", f"{API}/shared/trips/{trip_a['id']}", token_b)
        assert shared_summary.status_code == 200, shared_summary.text
        assert shared_summary.json()["scope"] == "TRIP_SUMMARY"
        assert shared_summary.json()["plan_nodes"] == []

        plan_share = await request("POST", f"{API}/trips/{trip_a['id']}/shares", token_a, json={
            "friend_user_id": users[1][0], "scope": "TRIP_AND_PLAN",
        })
        assert plan_share.status_code == 201, plan_share.text
        shared_plan = await request("GET", f"{API}/shared/trips/{trip_a['id']}", token_b)
        assert shared_plan.status_code == 200 and shared_plan.json()["scope"] == "TRIP_AND_PLAN", shared_plan.text
        assert shared_plan.json()["plan_nodes"], shared_plan.text
        assert (await request("GET", f"{API}/trips/{trip_a['id']}", token_b)).status_code == 404
        assert (await request("PATCH", f"{API}/trips/{trip_a['id']}", token_b,
                              json={"destination": "不应写入"})).status_code == 404
        assert (await request("GET", f"{API}/trips/{trip_a['id']}/wishlist", token_b)).status_code == 404

        revoked = await request("DELETE", f"{API}/trips/{trip_a['id']}/shares/{plan_share.json()['id']}", token_a)
        assert revoked.status_code == 204, revoked.text
        assert (await request("GET", f"{API}/shared/trips/{trip_a['id']}", token_b)).status_code == 404
        # Restore the grant so account deletion also proves the grant and Trip cascade.
        active_share = await request("POST", f"{API}/trips/{trip_a['id']}/shares", token_a, json={
            "friend_user_id": users[1][0], "scope": "TRIP_AND_PLAN",
        })
        assert active_share.status_code == 201, active_share.text

        checks = [
            await client.get(f"{API}/trips/{trip_a['id']}/wishlist", headers={"Cookie": ""}),
            await request("GET", f"{API}/trips/{trip_a['id']}", token_b),
            await request("PATCH", f"{API}/trips/{trip_a['id']}", token_b, json={"destination": "越权"}),
            await request("DELETE", f"{API}/trips/{trip_a['id']}", token_b),
            await request("GET", f"{API}/trips/{trip_a['id']}/wishlist", token_b),
            await request("POST", f"{API}/trips/{trip_a['id']}/wishlist", token_b, json={
                "name": "越权收藏", "category": "ATTRACTION", "latitude": 30.0,
                "longitude": 104.0, "suggested_duration_min": 60,
            }),
            await request("PATCH", f"{API}/wishlist/{item_id}", token_b, json={"notes": "越权"}),
            await request("DELETE", f"{API}/wishlist/{item_id}", token_b),
            await request("POST", f"{API}/trips/{trip_a['id']}/plan-runs", token_b),
            await request("GET", f"{API}/trips/{trip_a['id']}/plans", token_b),
            await request("GET", f"{API}/trips/{trip_a['id']}/plans/{plan_id}", token_b),
            await request("PATCH", f"{API}/trips/{trip_a['id']}/plans/{plan_id}/nodes/{plan.json()['nodes'][0]['id']}",
                          token_b, json={"priority": 1}),
            await request("GET", f"{API}/trips/{trip_a['id']}/plans/{plan_id}/changes", token_b),
            await request("POST", f"{API}/trips/{trip_a['id']}/events", token_b, json={
                "event_type": "TIME_DELAY", "effective_at": "2026-11-01T10:00:00+08:00",
                "payload": {"delay_min": 30},
            }),
            await request("POST", f"{API}/trips/{trip_a['id']}/replan-runs", token_b,
                          json={"event_id": event_id}),
            await request("POST", f"{API}/trips/{trip_a['id']}/replan-jobs", token_b,
                          json={"event_id": event_id}),
            await request("GET", f"{API}/trips/{trip_a['id']}/plans/{plan_id}/diff", token_b),
            await request("POST", f"{API}/trips/{trip_a['id']}/agent-plan-runs", token_b, json={}),
            await request("GET", f"{API}/content/{content_id}", token_b),
            await request("GET", f"{API}/content/{content_id}/candidates", token_b),
            await request("POST", f"{API}/content/{content_id}/confirm", token_b,
                          json={"candidate_ids": imported_candidate_ids}),
            await request("GET", f"{API}/trips/{trip_a['id']}/memories", token_b),
        ]
        expected_statuses = [401, *([404] * (len(checks) - 1))]
        assert [response.status_code for response in checks] == expected_statuses, [
            (response.status_code, response.text) for response in checks
        ]

        export_b = await request("GET", f"{API}/account/export", token_b)
        assert export_b.status_code == 200, export_b.text
        assert {row["id"] for row in export_b.json()["trips"]} == {trip_b["id"]}
        assert all(row["id"] != trip_a["id"] for row in export_b.json()["trips"])
        assert any(row["grantee_user_id"] == users[1][0] for row in export_b.json()["data"]["trip_shares"])
        export_a = await request("GET", f"{API}/account/export", token_a)
        assert export_a.status_code == 200
        assert any(row["id"] == item_id for row in export_a.json()["data"]["wishlist"])
        assert any(row["id"] == plan_id for row in export_a.json()["data"]["plans"])
        assert any(row["id"] == moment_id for row in export_a.json()["data"]["travel_moments"])
        assert any(row["id"] == asset_id for row in export_a.json()["data"]["moment_media"])
        assert any(row["id"] == memory_id for row in export_a.json()["data"]["user_preference_memories"])

        deleted = await request("DELETE", f"{API}/account", token_a)
        assert deleted.status_code == 204, deleted.text
        assert (await request("GET", f"{API}/auth/me", token_a)).status_code == 401
        assert (await request("GET", f"{API}/shared/trips/{trip_a['id']}", token_b)).status_code == 404
        assert (await request("GET", f"{API}/media/{asset_id}", token_b)).status_code == 404
        assert (await request("GET", f"{API}/moments", token_b)).json() == []
        assert not (Path(settings.media_storage_dir) / "users" / owner_id / f"{asset_id}.jpg").exists()
        assert (await request("GET", f"{API}/trips/{trip_b['id']}", token_b)).status_code == 200
        async with AsyncSessionFactory() as session:
            remaining = await session.scalar(select(func.count()).select_from(TripModel).where(
                TripModel.owner_id == UUID(owner_id)
            ))
            assert remaining == 0
            assert await session.scalar(select(func.count()).select_from(WishlistItemModel).where(
                WishlistItemModel.trip_id == UUID(trip_a["id"])
            )) == 0
            assert await session.scalar(select(func.count()).select_from(TripPlanModel).where(
                TripPlanModel.trip_id == UUID(trip_a["id"])
            )) == 0
            assert await session.get(PoiModel, UUID(poi_id)) is None
            assert await session.scalar(select(func.count()).select_from(TravelMomentModel).where(
                TravelMomentModel.owner_user_id == UUID(owner_id)
            )) == 0
            assert await session.scalar(select(func.count()).select_from(MomentMediaModel).where(
                MomentMediaModel.owner_user_id == UUID(owner_id)
            )) == 0
            assert await session.scalar(select(func.count()).select_from(UserSessionModel).where(
                UserSessionModel.user_id == UUID(owner_id)
            )) == 0
            assert await session.scalar(select(func.count()).select_from(UserPreferenceMemoryModel).where(
                UserPreferenceMemoryModel.owner_id == UUID(owner_id)
            )) == 0
            assert await session.get(UserMemorySettingsModel, UUID(owner_id)) is None
            assert await session.get(UserModel, UUID(owner_id)) is None
        logout = await request("POST", f"{API}/auth/logout", token_b)
        assert logout.status_code == 204
        assert (await request("GET", f"{API}/auth/me", token_b)).status_code == 401
        login_again = await client.post(f"{API}/auth/login", json={
            "email": f"owner-b-{suffix}@example.invalid", "password": reset_password,
        })
        assert login_again.status_code == 200
        auth = {
            "session": login_again.cookies["travelpilot_session"],
            "csrf": login_again.cookies["travelpilot_csrf"],
        }
        await request("DELETE", f"{API}/account", auth)
        print("MULTI-USER FLOW OK: accounts → private/friend moments → photo ACL/EXIF cleanup → comments/reactions → Trip share scopes/revocation → export/deletion cleanup")


if __name__ == "__main__":
    asyncio.run(run())
