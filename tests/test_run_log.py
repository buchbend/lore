import re
from datetime import UTC, datetime

from lore_core.run_log import generate_run_id


def test_run_id_format():
    ts = datetime(2026, 4, 20, 14, 32, 5, tzinfo=UTC)
    run_id = generate_run_id(now=ts)
    assert re.fullmatch(r"2026-04-20T14-32-05-[a-z0-9]{6}", run_id), run_id


def test_run_id_uniqueness():
    ids = {generate_run_id() for _ in range(1000)}
    assert len(ids) == 1000
