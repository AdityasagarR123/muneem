"""Owns the current dataset, the ArangoDB connection and per-dataset caches.

Startup:  ArangoDB has a saved dataset?  -> load it.
          otherwise                        -> load the sample CSVs (generate if missing)
                                              and sync them to ArangoDB in the background.
Upload:   validated dataset replaces the current one and is synced to ArangoDB.
ArangoDB down at any point -> everything keeps working from memory; graph queries
fall back to the in-memory graph engine and say so.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import pandas as pd

from app.analytics.festivals import FestivalCalendar
from app.analytics.stock import StockParams, compute_stock
from app.config import Settings
from app.graph.backends import ArangoGraph, FallbackGraph, MemoryGraph
from app.ingest.readers import read_dir
from app.ingest.validate import ValidationReport, validate_tables
from app.store.dataset import Dataset

log = logging.getLogger("muneem.store")


class NoDataset(RuntimeError):
    pass


class DataManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.calendar = FestivalCalendar.load(settings.festivals_file)
        self.params = StockParams.from_settings(settings)
        self.ds: Dataset | None = None
        self.db = None
        self.arango_status = "disabled"  # disabled | connected | down
        self.arango_error: str | None = None
        self.sync_status = "n/a"  # n/a | syncing | synced | failed
        self.persisted_version: str | None = None
        # PostgreSQL (system of record)
        self.sql = None
        self.sql_status = "disabled"  # disabled | connected | down
        self.sql_error: str | None = None
        self.sql_sync = "n/a"
        self.sql_version: str | None = None
        self._lock = threading.RLock()
        self._cache: dict = {}

    # ---------------------------------------------------------------- lifecycle
    def startup(self) -> None:
        if self.settings.database_url:
            self.connect_sql()
            if self.sql is not None:
                try:
                    ds = self.sql.load_dataset()
                    if ds is not None:
                        self._set(ds, persisted=False)
                        self.sql_version, self.sql_sync = ds.version, "synced"
                        log.info("Loaded dataset %s from PostgreSQL", ds.version)
                except Exception as exc:
                    self._sql_failed(exc)
        if self.settings.arango_enabled:
            self.connect_arango()
            if self.db is not None and self.ds is not None:
                self.sync_status = "syncing"  # SQL is the source of truth: refresh the graph
                threading.Thread(target=self._sync, args=(self.ds,), daemon=True).start()
            elif self.db is not None:
                try:
                    from app.db.arango import load_dataset

                    ds = load_dataset(self.db)
                    if ds is not None:
                        self._set(ds, persisted=True)
                        log.info("Loaded dataset %s from ArangoDB", ds.version)
                except Exception as exc:
                    self._arango_failed(exc)
        if self.ds is None:
            report = self.load_sample()
            if not report.ok:
                log.error("Sample data failed validation: %s", report.error_summary)

    def connect_arango(self) -> bool:
        from app.db.arango import ArangoUnavailable, connect

        try:
            self.db = connect(self.settings)
            self.arango_status, self.arango_error = "connected", None
            log.info("Connected to ArangoDB database '%s'", self.settings.arango_db)
            return True
        except ArangoUnavailable as exc:
            self._arango_failed(exc)
            return False

    def connect_sql(self) -> bool:
        from app.db.sql import SqlStore

        try:
            self.sql = SqlStore(self.settings.database_url)
            self.sql.ping()
            self.sql_status, self.sql_error = "connected", None
            log.info("Connected to %s", self.sql.dialect)
            return True
        except Exception as exc:
            self._sql_failed(exc)
            return False

    def _sql_failed(self, exc: Exception) -> None:
        self.sql = None
        self.sql_status = "down"
        self.sql_error = f"{type(exc).__name__}: {str(exc)[:250]}"
        log.warning("PostgreSQL unavailable - running from memory: %s", self.sql_error)

    def _sync_sql(self, ds: Dataset) -> None:
        try:
            self.sql.save_dataset(ds)
            with self._lock:
                if self.ds is not None and self.ds.version == ds.version:
                    self.sql_version, self.sql_sync = ds.version, "synced"
            log.info("Dataset %s saved to PostgreSQL", ds.version)
        except Exception as exc:
            self.sql_sync = "failed"
            self._sql_failed(exc)

    def _arango_failed(self, exc: Exception) -> None:
        self.db = None
        self.arango_status = "down"
        self.arango_error = str(exc)[:300]
        log.warning("ArangoDB unavailable - running from memory: %s", self.arango_error)

    def load_sample(self, regenerate: bool = False) -> ValidationReport:
        d = self.settings.data_dir
        if regenerate or not (d / "items.csv").exists():
            from app.ingest.generator import generate

            generate(d)
        report, ds = validate_tables(read_dir(d), source="sample",
                                     max_rows=self.settings.max_rows,
                                     max_text=self.settings.max_text_len)
        if ds is not None:
            self.replace(ds)
        return report

    def replace(self, ds: Dataset) -> None:
        self._set(ds, persisted=False)
        if self.sql is not None:
            self.sql_sync = "syncing"
            threading.Thread(target=self._sync_sql, args=(ds,), daemon=True).start()
        if self.db is not None:
            self.sync_status = "syncing"
            threading.Thread(target=self._sync, args=(ds,), daemon=True).start()

    def _set(self, ds: Dataset, persisted: bool) -> None:
        with self._lock:
            self.ds = ds
            self._cache.clear()
            if persisted:
                self.persisted_version = ds.version
                self.sync_status = "synced"

    def _sync(self, ds: Dataset) -> None:
        from app.db.arango import save_dataset

        try:
            save_dataset(self.db, ds)
            with self._lock:
                if self.ds is not None and self.ds.version == ds.version:
                    self.persisted_version = ds.version
                    self.sync_status = "synced"
                    self._cache.pop("graph", None)
            log.info("Dataset %s synced to ArangoDB", ds.version)
        except Exception as exc:
            self.sync_status = "failed"
            self._arango_failed(exc)

    def sync_now(self) -> None:
        """Synchronous sync (used by tests / admin)."""
        if self.sql is not None and self.ds is not None:
            self.sql_sync = "syncing"
            self._sync_sql(self.ds)
        if self.db is not None and self.ds is not None:
            self.sync_status = "syncing"
            self._sync(self.ds)

    # ---------------------------------------------------------------- access
    def require(self) -> Dataset:
        if self.ds is None:
            raise NoDataset("No data loaded yet - upload files or load the sample data")
        return self.ds

    def _cached(self, key, fn):
        ds = self.require()
        k = (ds.version, key)
        with self._lock:
            if k in self._cache:
                return self._cache[k]
        val = fn(ds)
        with self._lock:
            self._cache[k] = val
        return val

    def stock(self) -> pd.DataFrame:
        return self._cached("stock", lambda ds: compute_stock(ds, self.params, self.calendar))

    def customer_stats(self) -> pd.DataFrame:
        from app.analytics.customers import customer_stats

        return self._cached("customer_stats", customer_stats)

    def segments(self) -> dict[str, str]:
        from app.analytics.customers import segment_map

        return self._cached("segment_map", lambda ds: segment_map(self.customer_stats()))

    # ---------------------------------------------------------------- insights
    def findings(self) -> list:
        """All current findings (cached per dataset version) with their saved status."""
        from app.analytics.findings import run_detectors

        def build(ds):
            st = self.customer_stats()
            risk = set(st.loc[st["at_risk"], "customer_id"]) if len(st) else set()
            return run_detectors({"ds": ds, "cal": self.calendar, "stock": self.stock(), "stats": st,
                                  "risk_ids": risk, "graph": self.graph(),
                                  "dead_days": self.settings.dead_stock_days})

        found = self._cached("findings", build)
        statuses = self._insight_statuses()
        return [f.model_copy(update={"status": statuses.get(f.id, "new")}) for f in found]

    def _insight_statuses(self) -> dict[str, str]:
        if not hasattr(self, "_statuses"):
            self._statuses: dict[str, str] = {}
            if self.sql is not None:
                try:
                    self._statuses = self.sql.insight_statuses()
                except Exception as exc:  # pragma: no cover
                    log.warning("could not load insight statuses: %s", exc)
        return self._statuses

    def set_insight_status(self, insight_id: str, status: str) -> None:
        self._insight_statuses()[insight_id] = status
        if self.sql is not None:
            try:
                self.sql.set_insight_status(insight_id, status)
            except Exception as exc:  # pragma: no cover
                log.warning("could not save insight status: %s", exc)

    def graph(self) -> FallbackGraph:
        ds = self.require()
        with self._lock:
            g = self._cache.get("graph")
            if g is not None and g.memory.ds is ds:
                return g
            primary = (ArangoGraph(self.db) if self.db is not None
                       and self.persisted_version == ds.version else None)
            g = FallbackGraph(primary, MemoryGraph(ds))
            self._cache["graph"] = g
            return g

    @property
    def restock(self):
        """Purchase-order store (created on first use)."""
        if getattr(self, "_restock", None) is None:
            from app.actions.restock import RestockStore

            self._restock = RestockStore(self)
        return self._restock

    @property
    def campaigns(self):
        """Campaign store (created on first use)."""
        if getattr(self, "_campaigns", None) is None:
            from app.actions.campaigns import CampaignStore

            self._campaigns = CampaignStore(self)
        return self._campaigns

    def receive_stock(self, additions: dict[str, int]) -> Dataset:
        """Goods arrived: add quantities to stock. Creates a new dataset version so every
        alert, reorder plan and graph answer recomputes; synced to ArangoDB like an upload."""
        ds = self.require()
        unknown = set(additions) - set(ds.items["item_id"])
        if unknown:
            raise ValueError(f"Unknown items: {', '.join(sorted(unknown))}")
        inv = ds.inventory.copy()
        have = set(inv["item_id"])
        missing = [i for i in additions if i not in have]
        if missing:
            inv = pd.concat([inv, pd.DataFrame({"item_id": missing, "stock_qty": 0.0,
                                                "as_of_date": pd.Timestamp(ds.as_of)})],
                            ignore_index=True)
        inv["stock_qty"] = inv["stock_qty"].clip(lower=0) + inv["item_id"].map(additions).fillna(0)
        new = Dataset(items=ds.items, sales=ds.sales, inventory=inv, expenses=ds.expenses,
                      suppliers=ds.suppliers, as_of=ds.as_of, source=ds.source,
                      customers=ds.customers, repayments=ds.repayments)
        self.replace(new)
        return new

    def record(self, collection: str, doc: dict) -> None:
        if self.sql is not None:
            self.sql.record(collection, doc)
        elif self.db is not None:
            from app.db.arango import record

            record(self.db, collection, doc)

    def status(self) -> dict:
        return {
            "dataset": self.ds.summary() if self.ds is not None else None,
            "postgres": {
                "configured": bool(self.settings.database_url),
                "status": self.sql_status,
                "dialect": self.sql.dialect if self.sql is not None else None,
                "sync": self.sql_sync,
                "persisted": self.ds is not None and self.sql_version == self.ds.version,
                "error": self.sql_error,
            },
            "arango": {
                "configured": self.settings.arango_enabled,
                "status": self.arango_status,
                "database": self.settings.arango_db if self.settings.arango_enabled else None,
                "sync": self.sync_status,
                "persisted": self.ds is not None and self.persisted_version == self.ds.version,
                "error": self.arango_error,
            },
            "graph_engine": ("arangodb" if self.db is not None and self.ds is not None
                             and self.persisted_version == self.ds.version else "memory-graph"),
            "time": datetime.now(timezone.utc).isoformat(),
        }
