"""Room auth store: paired tokens with names (fix slice #3).

The session_token file holds bare token strings (legacy) or
{token, name, paired_at} records. Reads tolerate both plus
missing/corrupt files; writes normalize to records. No server
import needed — the store lives in auth.py on purpose.
"""
import json

import auth


def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return auth.session_token_path()


def test_round_trip_with_name(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    auth.save_client("tok-1", "muse")
    clients = auth.load_clients()
    assert [c["token"] for c in clients] == ["tok-1"]
    assert clients[0]["name"] == "muse"
    assert isinstance(clients[0]["paired_at"], float)
    assert auth.load_tokens() == ["tok-1"]
    assert auth.name_for_token("tok-1") == "muse"


def test_pairing_additive_and_first_name_wins(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    auth.save_client("tok-1", "muse")
    auth.save_client("tok-2", "cho-zen1")
    auth.save_client("tok-1", "intruder")  # re-save keeps first record
    assert auth.load_tokens() == ["tok-1", "tok-2"]
    assert auth.name_for_token("tok-1") == "muse"


def test_legacy_bare_strings_tolerated_and_named_guest(monkeypatch, tmp_path):
    p = _store(monkeypatch, tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(["tok-a", "tok-b"]) + "\n", encoding="utf-8")
    assert auth.load_tokens() == ["tok-a", "tok-b"]
    guests = [auth.name_for_token(t) for t in ("tok-a", "tok-b")]
    assert all(g.startswith("guest-") for g in guests)
    assert guests[0] != guests[1]  # distinct per token
    assert auth.name_for_token("tok-a") == guests[0]  # deterministic


def test_missing_and_corrupt_store_yields_no_sessions(monkeypatch, tmp_path):
    p = _store(monkeypatch, tmp_path)
    assert auth.load_clients() == []  # missing file
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json{{", encoding="utf-8")
    assert auth.load_clients() == []
    assert auth.name_for_token("anything") is None


def test_malformed_entries_skipped(monkeypatch, tmp_path):
    p = _store(monkeypatch, tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(["", {}, {"token": ""}, {"name": "x"},
                             {"token": "good", "name": "ok"}]),
                 encoding="utf-8")
    assert auth.load_tokens() == ["good"]


def test_name_normalization_and_guest_reservation(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    assert auth.normalize_name("  Muse ") == "muse"
    assert auth.normalize_name("has space") is None
    assert auth.normalize_name("has.dot") is None
    assert auth.normalize_name("x" * 33) is None
    assert auth.normalize_name("") is None
    assert auth.normalize_name(None) is None
    assert auth.normalize_name("guest-1") is None  # reserved prefix
    assert auth.normalize_name("Guest-X") is None  # case-insensitive
    auth.save_client("tok-1", "Bad Name!")
    assert auth.name_for_token("tok-1").startswith("guest-")


def test_revoke_one_and_clear_all(monkeypatch, tmp_path):
    p = _store(monkeypatch, tmp_path)
    auth.save_client("tok-1", "muse")
    auth.save_client("tok-2", "cho-zen1")
    assert auth.revoke_client("tok-1") is True
    assert auth.revoke_client("tok-1") is False
    assert auth.load_tokens() == ["tok-2"]
    auth.clear_clients()
    assert not p.exists()
    assert auth.load_tokens() == []


def test_writes_normalize_legacy_to_records(monkeypatch, tmp_path):
    p = _store(monkeypatch, tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(["tok-a"]) + "\n", encoding="utf-8")
    auth.save_client("tok-b", "muse")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert {d["token"] for d in data} == {"tok-a", "tok-b"}
    assert all(set(d) == {"token", "name", "paired_at"} for d in data)
