from __future__ import annotations

from datetime import UTC, datetime

from soulsaka.db import devices as devices_db
from soulsaka.util.ids import new_uid
from soulsaka.util.time import now_iso


def test_health_and_auth(client):
    assert client.get("/api/health").json()["ok"] is True
    # loopback + client header is trusted
    assert client.get("/api/me").json()["uid"] == "local"
    # without the header, loopback is not trusted
    r = client.get("/api/me", headers={"X-Soulsaka-Client": ""})
    assert r.status_code == 401
    # bad token
    r = client.get("/api/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_pairing_flow(client, state):
    code = devices_db.create_pairing_code(state.db)
    r = client.post("/api/pair", json={"code": code.lower(), "name": "phone", "kind": "browser"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # code is single use
    assert client.post("/api/pair", json={"code": code, "name": "again"}).status_code == 400
    me = client.get(
        "/api/me", headers={"Authorization": f"Bearer {token}", "X-Soulsaka-Client": ""}
    )
    assert me.status_code == 200 and me.json()["name"] == "phone"
    devs = client.get("/api/devices").json()
    assert any(d["name"] == "phone" for d in devs)
    uid = [d for d in devs if d["name"] == "phone"][0]["uid"]
    assert client.delete(f"/api/devices/{uid}").json()["ok"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_text_capture_creates_message_and_memory(client, runner):
    uid = new_uid()
    body = {
        "uid": uid,
        "kind": "text",
        "client_ts": now_iso(),
        "text": "remember my locker code is 4521",
    }
    r = client.post("/api/captures", json=body)
    assert r.status_code == 201, r.text
    # idempotent resend
    assert client.post("/api/captures", json=body).status_code == 200
    runner.drain()
    cap = client.get(f"/api/captures/{uid}").json()
    assert cap["status"] == "done", cap
    assert len(cap["memory_uids"]) == 1
    mems = client.get("/api/memories").json()
    assert mems[0]["kind"] == "number" and "4521" in mems[0]["text"]
    stats = client.get("/api/stats").json()
    assert stats["me_words"] == 6 and stats["by_register"][0]["register"] == "text"
    # sync sees it
    sync = client.get("/api/sync").json()
    assert sync["memories"][0]["uid"] == mems[0]["uid"]
    assert sync["captures"][0]["uid"] == uid


def test_memories_crud_and_search(client, runner):
    r = client.post(
        "/api/memories", json={"text": "Dentist appointment on Thursday at 3pm", "kind": "event"}
    )
    assert r.status_code == 201
    uid = r.json()["uid"]
    client.post("/api/memories", json={"text": "Prefers oat milk in coffee", "kind": "preference"})
    runner.drain()  # embeddings
    hits = client.get("/api/memories", params={"q": "dentist"}).json()
    assert hits and hits[0]["uid"] == uid
    upd = client.patch(f"/api/memories/{uid}", json={"text": "Dentist on Friday at 3pm"}).json()
    assert "Friday" in upd["text"]
    assert client.delete(f"/api/memories/{uid}").json()["ok"]
    assert client.get(f"/api/memories/{uid}").status_code == 404


def test_message_batch_push(client):
    batch = {
        "source": {"kind": "whatsapp", "label": "WhatsApp export", "locator": "chat.txt"},
        "messages": [
            {
                "conversation_external_id": "c",
                "text": "hey",
                "ts": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
                "is_me": False,
                "sender_name": "Ali",
            },
            {
                "conversation_external_id": "c",
                "text": "yo what's good",
                "ts": datetime(2025, 1, 1, 0, 1, tzinfo=UTC).isoformat(),
                "is_me": True,
            },
        ],
    }
    r = client.post("/api/messages/batch", json=batch)
    assert r.status_code == 200, r.text
    assert r.json()["inserted"] == 2 and r.json()["me_words"] == 3
    srcs = client.get("/api/sources").json()
    assert srcs[0]["me_words"] == 3
    assert (
        client.get("/api/messages/search", params={"q": "good"}).json()[0]["text"]
        == "yo what's good"
    )


def test_chat_refuses_cloud_without_switch(client):
    r = client.post("/api/chat", json={"text": "hi", "profile": "claude", "stream": False})
    assert r.status_code == 403
    profiles = client.get("/api/llm/profiles").json()
    assert {p["name"] for p in profiles} >= {"local", "claude", "openai", "claude-cli"}
    assert all(not p["enabled"] for p in profiles if p["cloud"])
