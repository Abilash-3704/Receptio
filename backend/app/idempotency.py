"""Idempotency for writes. The key is claimed (primary key insert) in the same transaction as
the work, so two concurrent requests with one key cannot both run: the second waits for the
first to commit, then replays its stored result. A rejected request stores nothing, so it can
be retried once the cause is fixed."""

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db import IdempotencyRecord


def run_idempotent(
    session: Session,
    key: str,
    scope: str,
    payload: dict[str, Any],
    work: Callable[[], tuple[int, BaseModel]],
) -> JSONResponse:
    request_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    claim = IdempotencyRecord(key=key, scope=scope, request_hash=request_hash)
    session.add(claim)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        stored = session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.key == key)
        ).one()
        if stored.scope != scope or stored.request_hash != request_hash:
            raise HTTPException(
                status_code=422, detail="this Idempotency-Key was used for a different request"
            ) from None
        assert stored.status_code is not None and stored.response is not None
        return JSONResponse(stored.response, status_code=stored.status_code)
    status_code, result = work()
    body = result.model_dump(mode="json")
    claim.status_code = status_code
    claim.response = body
    session.commit()
    return JSONResponse(body, status_code=status_code)
