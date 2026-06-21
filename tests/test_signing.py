"""Ed25519 manifest signing + trust-tier assessment (verified vs community)."""

import base64
import json

import pytest
from nacl.signing import SigningKey

import federation
from federation import assess_trust, canonical_bytes, verify_signature
from tools.sign_manifest import sign


def _manifest():
    return {
        "manifest_version": 2, "id": "c", "title": "C",
        "publisher": {"id": "jane", "name": "Jane"},
        "items": [{"id": "a", "title": "A", "image": {"full_url": "https://x/a.jpg", "license": "CC0-1.0"}}],
    }


def _sign(m, sk):
    m = json.loads(json.dumps(m))  # deep copy
    m.setdefault("publisher", {})["public_key"] = base64.b64encode(bytes(sk.verify_key)).decode()
    m["signature"] = base64.b64encode(sk.sign(canonical_bytes(m)).signature).decode()
    return m


def test_canonical_bytes_excludes_signature():
    m = _manifest()
    assert canonical_bytes(m) == canonical_bytes(dict(m, signature="anything"))


def test_verify_valid_signature():
    assert verify_signature(_sign(_manifest(), SigningKey.generate()))


def test_verify_detects_tampering():
    signed = _sign(_manifest(), SigningKey.generate())
    tampered = dict(signed, title="Hacked")
    assert not verify_signature(tampered)


def test_unsigned_manifest_does_not_verify():
    assert not verify_signature(_manifest())


def test_assess_trust_tiers():
    sk = SigningKey.generate()
    pub_b64 = base64.b64encode(bytes(sk.verify_key)).decode()
    signed = _sign(_manifest(), sk)
    assert assess_trust(_manifest()) == "community"                          # unsigned
    assert assess_trust(signed, trusted_keys={}) == "community"              # signed, not in registry (TOFU)
    assert assess_trust(signed, trusted_keys={"jane": pub_b64}) == "verified"  # signed + registry key
    # a registry entry for a *different* key does not promote
    assert assess_trust(signed, trusted_keys={"jane": "AAAA"}) == "community"


def test_sign_cli_roundtrip(tmp_path):
    sk = SigningKey.generate()
    priv = base64.b64encode(bytes(sk)).decode()
    path = tmp_path / "m.json"
    path.write_text(json.dumps(_manifest()))
    sign(str(path), priv, None)
    out = json.loads(path.read_text())
    assert "signature" in out and out["publisher"]["public_key"]
    assert verify_signature(out)


# --- fetch_manifest rejects a tampered signature ----------------------------

class _FakeStream:
    def __init__(self, body):
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self._body = body

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def aiter_bytes(self):
        yield self._body


@pytest.mark.asyncio
async def test_fetch_rejects_tampered_signature(monkeypatch):
    signed = _sign(_manifest(), SigningKey.generate())
    signed["title"] = "Hacked after signing"  # invalidates the signature
    monkeypatch.setattr(federation, "_assert_public_url", lambda url: None)

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return _FakeStream(json.dumps(signed).encode())
    monkeypatch.setattr(federation.httpx, "AsyncClient", _Client)

    with pytest.raises(federation.FederationError):
        await federation.fetch_manifest("https://jane.test/m.json")
