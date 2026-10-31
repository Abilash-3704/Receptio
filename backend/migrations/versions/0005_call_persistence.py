"""Call persistence: outcome, turns, tool calls, LLM calls, voice latency, cost.

The JSON transcript on `calls` becomes one `call_turns` row per utterance. Existing Calls keep
their transcript (copied row by row, in order) and have no recorded outcome.

Revision ID: 0005
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"

_TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("calls", sa.Column("mode", sa.String(8), nullable=False, server_default="text"))
    op.add_column("calls", sa.Column("outcome", sa.String(16)))
    op.add_column("calls", sa.Column("outcome_reason", sa.String(200)))
    op.add_column("calls", sa.Column("cost", sa.JSON))

    turns: sa.Table = op.create_table(  # pyright: ignore[reportUnknownVariableType]
        "call_turns",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), nullable=False, index=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("speaker", sa.String(8), nullable=False),
        sa.Column("text", sa.String(2000), nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False),
        sa.UniqueConstraint("call_id", "seq"),
    )
    connection = op.get_bind()
    old = connection.execute(sa.text("SELECT id, started_at, transcript FROM calls ORDER BY id"))
    copied: list[dict[str, object]] = []
    for call_id, started_at, transcript in old:
        utterances = json.loads(transcript) if isinstance(transcript, str) else transcript
        for seq, utterance in enumerate(utterances or [], start=1):
            copied.append(
                {
                    "call_id": call_id,
                    "seq": seq,
                    "speaker": utterance["speaker"],
                    "text": utterance["text"],
                    "created_at": started_at,  # the old record kept no per-turn time
                }
            )
    if copied:
        op.bulk_insert(turns, copied)
    op.drop_column("calls", "transcript")

    op.create_table(
        "call_tool_calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), nullable=False, index=True),
        sa.Column("turn_seq", sa.Integer, nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("args", sa.JSON, nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
        sa.Column("refused", sa.Boolean, nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False),
    )
    op.create_table(
        "call_llm_calls",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), nullable=False, index=True),
        sa.Column("turn_seq", sa.Integer, nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("cost_usd", sa.Float()),
        sa.Column("price_placeholder", sa.Boolean, nullable=False),
        sa.Column("created_at", _TIMESTAMP, nullable=False),
    )
    op.create_table(
        "call_turn_timings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("call_id", sa.Integer, sa.ForeignKey("calls.id"), nullable=False, index=True),
        sa.Column("caller_turn_seq", sa.Integer),
        sa.Column("asr_final_ms", sa.Integer),
        sa.Column("llm_first_token_ms", sa.Integer),
        sa.Column("tts_first_audio_ms", sa.Integer),
        sa.Column("total_ms", sa.Integer),
        sa.Column("total_from_speech_end_ms", sa.Integer),
        sa.Column("interrupted", sa.Boolean, nullable=False),
    )


def downgrade() -> None:
    op.add_column("calls", sa.Column("transcript", sa.JSON))
    connection = op.get_bind()
    turns = connection.execute(
        sa.text("SELECT call_id, speaker, text FROM call_turns ORDER BY call_id, seq")
    ).all()
    by_call: dict[int, list[dict[str, str]]] = {}
    for call_id, speaker, text in turns:
        by_call.setdefault(call_id, []).append({"speaker": speaker, "text": text})
    for (call_id,) in connection.execute(sa.text("SELECT id FROM calls")).all():
        connection.execute(
            sa.text("UPDATE calls SET transcript = CAST(:t AS json) WHERE id = :id"),
            {"t": json.dumps(by_call.get(call_id, [])), "id": call_id},
        )
    op.alter_column("calls", "transcript", nullable=False)
    for table in ("call_turn_timings", "call_llm_calls", "call_tool_calls", "call_turns"):
        op.drop_table(table)
    for column in ("cost", "outcome_reason", "outcome", "mode"):
        op.drop_column("calls", column)
