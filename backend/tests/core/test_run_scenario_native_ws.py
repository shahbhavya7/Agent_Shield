"""Integration test: the REAL run_scenario() loop driving a native_ws agent.

app.core.runner.run_scenario() normally persists to Postgres via app.db — there is
no test database in this environment, so its three DB calls are replaced with an
in-memory fake here. Everything else is the genuine, unmodified code path: the real
run_scenario() turn loop, the real call_voice_agent()/close_voice_session()
dispatch, and the real _call_via_native_ws() transport (with only httpx/websockets
faked, as in test_voice_native_ws.py).
"""
import pytest

from app.core import runner, voice_caller
from app.core import voice_native_ws as nws
from tests.core.fakes_native_ws import FakeAsyncClient, FakeConnect, FakeWebSocket, turn_frame


@pytest.fixture(autouse=True)
def _clear_sessions():
    nws._SESSIONS.clear()
    yield
    nws._SESSIONS.clear()


class _FakeDB:
    """Conversations keyed by (run_id, scenario_id) — enough to fake idempotency
    for what these tests exercise."""

    def __init__(self):
        self.messages: dict[int, list[dict]] = {}
        self._next_id = 1
        self._by_key: dict[tuple, int] = {}

    def get_or_create_conversation(self, run_id, scenario_id, idem_key=None):
        key = idem_key or (run_id, scenario_id, self._next_id)
        if key in self._by_key:
            return self._by_key[key]
        cid = self._next_id
        self._next_id += 1
        self._by_key[key] = cid
        self.messages[cid] = []
        return cid

    def clear_messages(self, conversation_id):
        n = len(self.messages.get(conversation_id, []))
        self.messages[conversation_id] = []
        return n

    def insert_message(self, conversation_id, turn_index, role, content, trace):
        self.messages[conversation_id].append(
            {"turn_index": turn_index, "role": role, "content": content, "trace": trace}
        )
        return len(self.messages[conversation_id])


@pytest.fixture()
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(runner, "get_or_create_conversation", db.get_or_create_conversation)
    monkeypatch.setattr(runner, "clear_messages", db.clear_messages)
    monkeypatch.setattr(runner, "insert_message", db.insert_message)
    return db


def _agent(**overrides) -> dict:
    base = {
        "voice_protocol": "native_ws",
        "endpoint_url": "http://localhost:8000",
        "auth_header": "Authorization: Bearer qa-token",
        "request_template": '{"client": "acme_health"}',
    }
    base.update(overrides)
    return base


def _wire_native_ws(monkeypatch, ws: FakeWebSocket, call_id="call-1"):
    connect = FakeConnect([ws])
    calls_log: list[dict] = []
    http_client = FakeAsyncClient(calls_log, call_id=call_id)
    monkeypatch.setattr(nws.websockets, "connect", connect)
    monkeypatch.setattr(nws.httpx, "AsyncClient", http_client.factory())
    return connect, calls_log


async def test_multi_turn_scenario_reuses_one_connection_and_records_history(monkeypatch, fake_db):
    ws = FakeWebSocket(incoming=[
        turn_frame("agent", "Hi, scheduling line."),
        turn_frame("caller", "I need an appointment"),
        turn_frame("agent", "What day works for you?"),
        turn_frame("caller", "Tuesday please"),
        turn_frame("agent", "Tuesday is booked."),
    ])
    connect, calls_log = _wire_native_ws(monkeypatch, ws)

    scenario = {
        "_id": 42,
        "test_type": "support",
        "assigned_fault": "none",
        "seed_turns": ["I need an appointment", "Tuesday please"],
    }

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=_agent(),
        idem_key="run:1:scenario:42:voice",
        send_fn=voice_caller.call_voice_agent,
        close_fn=voice_caller.close_voice_session,
    )

    transcript = fake_db.messages[conv_id]
    assert [m["role"] for m in transcript] == ["tester", "agent", "tester", "agent"]
    assert transcript[0]["content"] == "I need an appointment"
    assert transcript[1]["content"] == "What day works for you?"
    assert transcript[1]["trace"]["opening_line"] == "Hi, scheduling line."
    assert transcript[2]["content"] == "Tuesday please"
    assert transcript[3]["content"] == "Tuesday is booked."
    assert "opening_line" not in (transcript[3]["trace"] or {})

    # ONE connection, ONE call, for the whole two-turn scenario.
    assert connect.call_count == 1
    create_calls = [c for c in calls_log if c["url"].endswith("/api/calls")]
    assert len(create_calls) == 1

    # run_scenario's finally guarantees close_fn ran: socket closed 1000, /end called.
    assert ws.closed is True
    assert ws.close_args[0] == 1000
    end_calls = [c for c in calls_log if c["url"].endswith("/end")]
    assert len(end_calls) == 1


async def test_scenario_failure_still_closes_the_session(monkeypatch, fake_db):
    """The socket dies mid-scenario (second turn has nothing queued to read) —
    run_scenario() must still reach its finally and close the (now-dead) session
    without raising, and cleanup must be a harmless no-op for an already-tombstoned
    session."""
    ws = FakeWebSocket(incoming=[
        turn_frame("agent", "Hi."),
        turn_frame("caller", "hello"),
        turn_frame("agent", "How can I help?"),
        # nothing queued for turn 2 -> recv() raises inside _call_via_native_ws
    ])
    connect, calls_log = _wire_native_ws(monkeypatch, ws)

    scenario = {
        "_id": 43,
        "test_type": "support",
        "assigned_fault": "none",
        "seed_turns": ["hello", "are you still there"],
    }

    conv_id = await runner.run_scenario(
        run_id=1, scenario=scenario, agent=_agent(),
        idem_key="run:1:scenario:43:voice",
        send_fn=voice_caller.call_voice_agent,
        close_fn=voice_caller.close_voice_session,
    )

    transcript = fake_db.messages[conv_id]
    assert transcript[1]["content"] == "How can I help?"
    assert transcript[3]["content"] == voice_caller.AGENT_ERROR_SENTINEL

    # Cleanup still ran exactly once despite the mid-scenario failure.
    assert ws.closed is True
    end_calls = [c for c in calls_log if c["url"].endswith("/end")]
    assert len(end_calls) == 1
