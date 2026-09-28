"""Regression guards for the N+1 -> batched lookups in ``SessionDB``.

Each test pins two things: results are identical to the legacy per-row
algorithm, and the number of SQL statements no longer grows with N.
"""
import json
import random
import time

import pytest

from openagents_state import SessionDB
from openagents_state_common import _SQL_IN_CHUNK


@pytest.fixture
def db(tmp_path):
    d = SessionDB(tmp_path / "state.db")
    yield d
    d.close()


class _CountingConn:
    """Forwarding proxy that records every top-level ``execute`` SQL text."""

    def __init__(self, real, sink):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_sink", sink)

    def execute(self, sql, *args, **kwargs):
        self._sink.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


class _StatementCounter:
    """Count top-level SELECT/UPDATE/DELETE statements issued via ``db._conn``.

    Swaps ``db._conn`` for a proxy so trigger sub-statements (FTS) are not
    counted — only statements the Python code actually issued.
    """

    def __init__(self, db, prefixes=("SELECT", "WITH", "UPDATE", "DELETE")):
        self.db = db
        self.prefixes = prefixes
        self.raw = []

    def __enter__(self):
        self.real = self.db._conn
        self.db._conn = _CountingConn(self.real, self.raw)
        # Force reads through db._conn (disable the WAL read pool) so the counter
        # observes every SELECT the batched functions issue — the pooled reader
        # would otherwise bypass db._conn and report 0 statements.
        self._saved_wal = self.db._wal_active
        self.db._wal_active = False
        return self

    def __exit__(self, *exc):
        self.db._conn = self.real
        self.db._wal_active = self._saved_wal

    @property
    def statements(self):
        return [s for s in self.raw if s.lstrip().upper().startswith(self.prefixes)]

    def count(self, needle=None):
        if needle is None:
            return len(self.statements)
        return sum(1 for s in self.statements if needle in s)


_CHAIN_STEP_LEGACY_SQL = """
            SELECT child.id
            FROM sessions parent
            JOIN sessions child ON child.parent_session_id = parent.id
            WHERE parent.id = ?
              AND parent.end_reason = 'compression'
              AND json_extract(COALESCE(child.model_config, '{}'), '$._branched_from') IS NULL
              AND json_extract(COALESCE(child.model_config, '{}'), '$._delegate_from') IS NULL
              AND COALESCE(child.source, '') != 'tool'
            ORDER BY
              CASE
                WHEN child.end_reason = 'compression' THEN 0
                WHEN child.ended_at IS NULL THEN 1
                ELSE 2
              END,
              COALESCE(
                (SELECT MAX(m.timestamp) FROM messages m WHERE m.session_id = child.id),
                child.started_at
              ) DESC,
              child.started_at DESC,
              child.id DESC
            LIMIT 1
            """


def _legacy_compression_chain(db: SessionDB, session_id):
    """Verbatim copy of the pre-batching single-session chain walk, returning the full chain."""
    current = session_id
    chain = [current] if current else []
    seen = set(chain)
    for _ in range(100):
        row = db._conn.execute(_CHAIN_STEP_LEGACY_SQL, (current,)).fetchone()
        if row is None:
            return chain
        child_id = row["id"]
        if not child_id or child_id in seen:
            return chain
        seen.add(child_id)
        current = child_id
        chain.append(child_id)
    return chain


def _legacy_compression_tip(db: SessionDB, session_id):
    chain = _legacy_compression_chain(db, session_id)
    return chain[-1] if chain else session_id


def _set(db, sid, **cols):
    sets = ", ".join(f"{k} = ?" for k in cols)
    db._conn.execute(f"UPDATE sessions SET {sets} WHERE id = ?", (*cols.values(), sid))
    db._conn.commit()


def _build_random_forest(db: SessionDB, seed: int, n: int = 60):
    rng = random.Random(seed)
    base = 1_700_000_000
    ids = [f"s{seed}_{i:03d}" for i in range(n)]
    for i, sid in enumerate(ids):
        parent = rng.choice(ids[:i]) if i and rng.random() < 0.8 else None
        db.create_session(sid, source=rng.choice(["cli", "cli", "tool", "telegram"]),
                          parent_session_id=parent)
        cols = {"started_at": base + rng.choice([i, i, 5])}  # force some ties
        r = rng.random()
        if r < 0.55:
            cols["end_reason"] = "compression"
            cols["ended_at"] = base + i + 1
        elif r < 0.7:
            cols["end_reason"] = "user_exit"
            cols["ended_at"] = base + i + 1
        mc = rng.random()
        if mc < 0.1:
            cols["model_config"] = json.dumps({"_branched_from": "x"})
        elif mc < 0.2:
            cols["model_config"] = json.dumps({"_delegate_from": "x"})
        # Append the message BEFORE the session is marked ended/compressed:
        # upstream's append_message now hard-guards compression-closed sessions.
        if rng.random() < 0.4:
            db.append_message(sid, role="user", content=f"hello {sid}")
        _set(db, sid, **cols)
    # Inject a cycle (pathological, but the walk must terminate identically).
    _set(db, ids[1], parent_session_id=ids[5], end_reason="compression")
    return ids


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_get_compression_tips_matches_legacy_walk(db, seed):
    ids = _build_random_forest(db, seed)
    probe = ids + ["missing-id", "", None]
    expected = {sid: _legacy_compression_tip(db, sid) for sid in probe}
    assert db.get_compression_tips(probe) == expected
    for sid in probe:
        assert db.get_compression_tip(sid) == expected[sid]


def test_get_compression_tips_query_count_scales_with_depth_not_n(db):
    base = 1_700_000_000
    roots = []
    for i in range(40):
        root, child = f"root{i}", f"root{i}_c"
        db.create_session(root, source="cli")
        db.create_session(child, source="cli", parent_session_id=root)
        _set(db, root, end_reason="compression", ended_at=base + i, started_at=base)
        roots.append(root)
    with _StatementCounter(db) as ctr:
        tips = db.get_compression_tips(roots)
    assert tips == {r: f"{r}_c" for r in roots}
    # depth 1 chain: one level query + one terminating level query.
    assert ctr.count() == 2


def test_get_compression_tips_empty_input_issues_no_query(db):
    with _StatementCounter(db) as ctr:
        assert db.get_compression_tips([]) == {}
    assert ctr.count() == 0


def test_list_sessions_rich_projection_batched_and_unchanged(db):
    base = 1_700_000_000
    n = 30
    for i in range(n):
        root, tip = f"r{i:02d}", f"r{i:02d}_tip"
        db.create_session(root, source="cli")
        db.append_message(root, role="user", content=f"root msg {i}")
        _set(db, root, end_reason="compression", ended_at=base + i + 1, started_at=base + i)
        db.create_session(tip, source="cli", parent_session_id=root)
        db.append_message(tip, role="user", content=f"tip msg {i}")
        _set(db, tip, started_at=base + i + 2)
    db.create_session("plain", source="cli")
    _set(db, "plain", started_at=base + 1000)

    # Legacy projection, computed with the per-row helpers.
    raw = db.list_sessions_rich(limit=100, project_compression_tips=False)
    expected = []
    for s in raw:
        if s.get("end_reason") != "compression":
            expected.append(s)
            continue
        tip_id = _legacy_compression_tip(db, s["id"])
        tip_row = db._get_session_rich_row(tip_id)
        merged = dict(s)
        for key in (
            "id", "ended_at", "end_reason", "message_count",
            "tool_call_count", "title", "last_active", "preview",
            "model", "system_prompt", "cwd", "git_branch", "git_repo_root",
        ):
            if key in tip_row:
                merged[key] = tip_row[key]
        merged["_lineage_root_id"] = s["id"]
        merged["_lineage_ids"] = _legacy_compression_chain(db, s["id"])
        expected.append(merged)

    with _StatementCounter(db) as ctr:
        got = db.list_sessions_rich(limit=100)
    assert got == expected
    assert {s["id"] for s in got} == {f"r{i:02d}_tip" for i in range(n)} | {"plain"}
    # list query + 2 chain-walk levels + 1 batched tip-row query; was ~3N+1.
    assert ctr.count() <= 4


def test_search_messages_context_matches_legacy_and_is_batched(db):
    base = time.time() - 1000
    for s in range(6):
        sid = f"sess{s}"
        db.create_session(sid, source="cli")
        for i in range(5):
            content = f"needle {s}-{i}" if i in (0, 2, 4) else f"filler {s}-{i}"
            if i == 3:
                content = [{"type": "text", "text": "multimodal part"}, {"type": "image_url"}]
            db.append_message(sid, role="user" if i % 2 == 0 else "assistant",
                              content=content)
        # Equal timestamps on two rows exercise the (timestamp, id) tie-break.
        db._conn.execute(
            "UPDATE messages SET timestamp = ? WHERE session_id = ?", (base + s, sid)
        )
    db._conn.commit()

    def legacy_context(mid):
        rows = db._conn.execute(
            """WITH target AS (SELECT session_id, timestamp, id FROM messages WHERE id = ?)
               SELECT role, content FROM (
                   SELECT m.id, m.timestamp, m.role, m.content FROM messages m
                   JOIN target t ON t.session_id = m.session_id
                   WHERE (m.timestamp < t.timestamp) OR (m.timestamp = t.timestamp AND m.id < t.id)
                   ORDER BY m.timestamp DESC, m.id DESC LIMIT 1)
               UNION ALL SELECT role, content FROM messages WHERE id = ?
               UNION ALL SELECT role, content FROM (
                   SELECT m.id, m.timestamp, m.role, m.content FROM messages m
                   JOIN target t ON t.session_id = m.session_id
                   WHERE (m.timestamp > t.timestamp) OR (m.timestamp = t.timestamp AND m.id > t.id)
                   ORDER BY m.timestamp ASC, m.id ASC LIMIT 1)""",
            (mid, mid),
        ).fetchall()
        out = []
        for r in rows:
            decoded = db._decode_content(r["content"])
            if isinstance(decoded, list):
                text = " ".join(
                    p.get("text", "") for p in decoded
                    if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
                ).strip()
                preview = text or "[multimodal content]"
            elif isinstance(decoded, str):
                preview = decoded
            else:
                preview = ""
            out.append({"role": r["role"], "content": preview[:200]})
        return out

    with _StatementCounter(db) as ctr:
        matches = db.search_messages("needle", limit=50)
    assert len(matches) == 18
    for m in matches:
        assert m["context"] == legacy_context(m["id"])
        assert len(m["context"]) in (2, 3)
    # Context statements no longer scale with the match count: one batched
    # _CONTEXT_WINDOW_SQL query (CTE over the match id batch), not one per hit.
    assert ctr.count("FROM target t JOIN messages m") == 1


def _make_prunable(db, n, prefix, empty):
    old = time.time() - 200 * 86400
    ids = []
    for i in range(n):
        sid = f"{prefix}{i}"
        db.create_session(sid, source="cli")
        if not empty:
            db.append_message(sid, role="user", content="x")
            # Stamp the message old too — prune matches on the freshest of
            # last_activity_at / latest message / started_at.
            db._conn.execute("UPDATE messages SET timestamp = ? WHERE session_id = ?", (old, sid))
        _set(db, sid, started_at=old, ended_at=old + 1, end_reason="user_exit", last_activity_at=old)
        ids.append(sid)
    return ids


def test_prune_sessions_is_set_based(db):
    ids = _make_prunable(db, 1200, "old", empty=False)  # > one IN chunk
    db.create_session("keep_child", source="cli", parent_session_id=ids[7])
    db.create_session("fresh", source="cli")
    with _StatementCounter(db, prefixes=("DELETE",)) as ctr:
        assert db.prune_sessions(older_than_days=90) == 1200
    # Chunked IN deletes (size _SQL_IN_CHUNK) instead of one DELETE per session.
    # (FTS triggers also surface in the trace, so count top-level deletes.)
    n_chunks = (1200 + _SQL_IN_CHUNK - 1) // _SQL_IN_CHUNK
    assert ctr.count("DELETE FROM messages WHERE session_id") == n_chunks
    assert ctr.count("DELETE FROM sessions WHERE id") == n_chunks
    remaining = {r["id"] for r in db._conn.execute("SELECT id FROM sessions")}
    assert remaining == {"keep_child", "fresh"}
    assert db.get_session("keep_child")["parent_session_id"] is None
    assert db._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_delete_empty_sessions_is_set_based(db):
    _make_prunable(db, 25, "ghost", empty=True)
    _make_prunable(db, 3, "full", empty=False)
    with _StatementCounter(db, prefixes=("DELETE",)) as ctr:
        assert db.delete_empty_sessions() == 25
    assert ctr.count("DELETE FROM messages WHERE session_id") == 1
    assert ctr.count("DELETE FROM sessions WHERE id") == 1
    remaining = {r["id"] for r in db._conn.execute("SELECT id FROM sessions")}
    assert remaining == {"full0", "full1", "full2"}


def test_get_sessions_by_ids(db):
    for i in range(3):
        db.create_session(f"g{i}", source="cli")
    with _StatementCounter(db) as ctr:
        rows = db.get_sessions_by_ids(["g0", "g2", "nope", "g0"])
    assert set(rows) == {"g0", "g2"}
    assert rows["g0"] == db.get_session("g0")
    assert ctr.count() == 1
    with _StatementCounter(db) as ctr:
        assert db.get_sessions_by_ids([]) == {}
    assert ctr.count() == 0
