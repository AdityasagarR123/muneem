import json
import os

# Unit tests never touch a real ArangoDB or a real LLM (live tests opt in explicitly).
os.environ["ARANGO_URL"] = ""
os.environ["LLM_API_KEY"] = ""

import pytest  # noqa: E402

from app.analytics.festivals import FestivalCalendar  # noqa: E402
from app.analytics.stock import StockParams, compute_stock  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.ingest.readers import read_dir  # noqa: E402
from app.ingest.validate import validate_tables  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    s = get_settings()
    if not (s.data_dir / "items.csv").exists():
        from app.ingest.generator import generate

        generate(s.data_dir)
    return s


@pytest.fixture(scope="session")
def expected(settings):
    return json.loads((settings.data_dir / "expected.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def ds(settings):
    report, ds = validate_tables(read_dir(settings.data_dir), source="sample")
    assert report.ok, report.error_summary
    return ds


@pytest.fixture(scope="session")
def cal(settings):
    return FestivalCalendar.load(settings.festivals_file)


@pytest.fixture(scope="session")
def stock_df(ds, cal):
    return compute_stock(ds, StockParams(), cal)
