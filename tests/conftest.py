import copy
import json
import os
from pathlib import Path

import pytest

os.environ["SERVICE_API_KEY"] = "test-key"
os.environ["AI_PROVIDER"] = "fake"

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "u01_l01.json"


@pytest.fixture
def sample() -> dict:
    return copy.deepcopy(json.loads(SAMPLE.read_text(encoding="utf-8")))
