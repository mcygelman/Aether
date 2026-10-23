import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_fabric import FixedClock, MemoryEngine  # noqa: E402

START = "2026-10-06T09:00:00Z"


@pytest.fixture
def clock():
    return FixedClock(START)


@pytest.fixture
def engine(clock):
    eng = MemoryEngine.create(":memory:", "grace", name="Grace", clock=clock)
    yield eng
    if not eng.cart._closed:
        eng.close()
