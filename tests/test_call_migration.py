"""The call persistence migration, on a real Postgres (set TEST_DATABASE_URL; skipped otherwise).
A database that already holds Calls with the old JSON transcript keeps them: every utterance
becomes a stored turn, in order."""

import json
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from backend.app.db import Base
from backend.app.migrate import ROOT
from tests.conftest import POSTGRES_URL

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="needs a real Postgres")


def migrate(connection: object, revision: str, direction: str = "upgrade") -> None:
    config = Config()
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.attributes["connection"] = connection
    getattr(command, direction)(config, revision)


def test_old_json_transcripts_become_turn_rows_and_the_new_tables_exist() -> None:
    assert POSTGRES_URL
    engine = create_engine(POSTGRES_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        migrate(conn, "0005")
        transcript = [
            {"speaker": "agent", "text": "Hello, I'm an AI."},
            {"speaker": "caller", "text": "Hi"},
        ]
        conn.execute(
            text(
                "INSERT INTO calls (status, started_at, ended_at, transcript)"
                " VALUES ('ended', :at, :at, CAST(:t AS json))"
            ),
            {"at": datetime(2026, 11, 2, 8, 0, tzinfo=UTC), "t": json.dumps(transcript)},
        )

        migrate(conn, "head")

        rows = conn.execute(text("SELECT seq, speaker, text FROM call_turns ORDER BY seq")).all()
        assert [tuple(r) for r in rows] == [(1, "agent", "Hello, I'm an AI."), (2, "caller", "Hi")]
        call = conn.execute(text("SELECT mode, outcome FROM calls")).one()
        assert tuple(call) == ("text", None)  # an old Call's outcome was never recorded
        tables = set(inspect(conn).get_table_names())
        assert {"call_turns", "call_tool_calls", "call_llm_calls", "call_turn_timings"} <= tables
        assert "transcript" not in {c["name"] for c in inspect(conn).get_columns("calls")}

        # What the migration built is what the models say, so the app can read and write it.
        for name in (
            "calls",
            "call_turns",
            "call_tool_calls",
            "call_llm_calls",
            "call_turn_timings",
        ):
            built = {c["name"] for c in inspect(conn).get_columns(name)}
            assert built == {c.name for c in Base.metadata.tables[name].columns}, name

        migrate(conn, "0005", "downgrade")
        restored = conn.execute(text("SELECT transcript FROM calls")).scalar_one()
        assert restored == transcript
    engine.dispose()
