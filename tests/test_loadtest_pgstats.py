"""The Postgres contention sampler (needs a real Postgres: set TEST_DATABASE_URL)."""

import time

import pytest
from sqlalchemy import create_engine, text

from loadtest.pgstats import PgSampler
from tests.conftest import POSTGRES_URL

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="needs TEST_DATABASE_URL")


def test_it_samples_connections_and_counts_the_transactions_made_meanwhile() -> None:
    assert POSTGRES_URL is not None
    engine = create_engine(POSTGRES_URL)
    with PgSampler(engine, every_seconds=0.02, settle_seconds=1.2) as sampler:
        for _ in range(5):
            with engine.begin() as connection:
                connection.execute(text("select 1"))
        time.sleep(0.15)
        engine.dispose()  # closing a connection publishes its counters at once

    assert sampler.stats.samples >= 2
    assert sampler.stats.commits >= 5
    assert sampler.stats.deadlocks == 0
    engine.dispose()
