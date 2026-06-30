"""Appliance update bridge — mode-gating, action whitelist, and request/status file protocol."""

import json

import pytest
from fastapi.testclient import TestClient

import config
from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the bridge at a throwaway dir so tests never touch the real ./data.
    monkeypatch.setattr(config, "APPLIANCE_DIR", tmp_path / "appliance")
    with TestClient(app) as c:
        yield c


def test_update_403_when_not_appliance(client, monkeypatch):
    monkeypatch.setattr(config, "IS_APPLIANCE", False)
    assert client.post("/api/appliance/update", json={"action": "update-app"}).status_code == 403
    assert client.get("/api/appliance/update/status").status_code == 403


def test_update_rejects_unknown_action(client, monkeypatch):
    monkeypatch.setattr(config, "IS_APPLIANCE", True)
    resp = client.post("/api/appliance/update", json={"action": "rm -rf /"})
    assert resp.status_code == 400


def test_update_queues_request_and_status(client, monkeypatch):
    monkeypatch.setattr(config, "IS_APPLIANCE", True)
    resp = client.post("/api/appliance/update", json={"action": "update-app"})
    assert resp.status_code == 200
    nonce = resp.json()["nonce"]
    assert nonce

    # request.json carries the action + matching nonce for the host helper.
    req = json.loads((config.APPLIANCE_DIR / "request.json").read_text())
    assert req["action"] == "update-app"
    assert req["nonce"] == nonce
    assert "requested_at" in req

    # status.json was written queued BEFORE the request (so the .path trigger always sees a status).
    status = client.get("/api/appliance/update/status").json()
    assert status["state"] == "queued"
    assert status["action"] == "update-app"
    assert status["nonce"] == nonce


def test_status_idle_when_no_request(client, monkeypatch):
    monkeypatch.setattr(config, "IS_APPLIANCE", True)
    assert client.get("/api/appliance/update/status").json() == {"state": "idle"}
