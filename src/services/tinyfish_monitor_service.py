#!/usr/bin/env python3
"""TinyFish internet search agent service for Clawcross.

Shared by:
- CLI script
- Flask REST endpoints
- scheduler jobs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import utils.scheduler_service
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
from utils.runtime_paths import CONFIG_DIR, DATA_DIR, ENV_FILE

DEFAULT_ENV_PATH = ENV_FILE
DEFAULT_DB_PATH = DATA_DIR / "tinyfish_monitor.db"
DEFAULT_TARGETS_PATH = CONFIG_DIR / "tinyfish_targets.json"
EXAMPLE_TARGETS_PATH = PROJECT_ROOT / "config" / "tinyfish_targets.example.json"

DEFAULT_BASE_URL = "https://agent.tinyfish.ai"
FINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
PENDING_STATUSES = {"QUEUED", "PENDING", "RUNNING", "IN_PROGRESS"}
PRICE_LIST_KEYS = ("items", "prices", "plans", "products", "results", "data")
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_MAX_WAIT_SECONDS = 900
DEFAULT_REQUEST_TIMEOUT = 60


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def load_env(env_path: Path = DEFAULT_ENV_PATH) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


load_env()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "target"


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", value.replace(" ", ""))
        if not match:
            return None
        try:
            return float(match.group(0).replace(",", ""))
        except ValueError:
            return None
    return None


def detect_currency(item: dict[str, Any], price_text: str | None) -> str | None:
    explicit = first_present(item, "currency", "currency_code", "price_currency")
    if explicit:
        return str(explicit)
    if not price_text:
        return None
    if "$" in price_text:
        return "USD"
    if "€" in price_text:
        return "EUR"
    if "£" in price_text:
        return "GBP"
    if "¥" in price_text or "JPY" in price_text.upper():
        return "JPY"
    return None


def detect_billing_period(item: dict[str, Any], price_text: str | None) -> str | None:
    explicit = first_present(item, "billing_period", "billing_cycle", "period", "interval")
    if explicit:
        return str(explicit)
    if not price_text:
        return None
    lowered = price_text.lower()
    if any(token in lowered for token in ("/month", " per month", "/mo", "monthly")):
        return "monthly"
    if any(token in lowered for token in ("/year", " per year", "/yr", "annually", "annual")):
        return "yearly"
    if any(token in lowered for token in ("/week", "weekly")):
        return "weekly"
    if any(token in lowered for token in ("/day", "daily")):
        return "daily"
    if "one-time" in lowered or "one time" in lowered:
        return "one-time"
    return None


def detect_availability(item: dict[str, Any]) -> str | None:
    value = first_present(item, "availability", "status", "in_stock", "available")
    if value is None:
        return None
    if isinstance(value, bool):
        return "available" if value else "unavailable"
    return str(value)


def build_default_goal(target_name: str, url: str) -> str:
    return (
        f"Inspect {target_name} starting from {url}. Extract the structured data from this page. "
        "Find all relevant items, entries, or data rows you can identify. "
        "Return strict JSON only with this schema: "
        '{"items":[{"item_key":"","name":"","plan_name":"","sku":"","price":"","currency":"","amount":0,'
        '"billing_period":"","availability":"","source_url":"","notes":""}]}'
    )


@dataclass
class Target:
    site_key: str
    name: str
    url: str
    goal: str
    browser_profile: str = "lite"
    extra_payload: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "url": self.url,
            "goal": self.goal,
            "browser_profile": self.browser_profile,
            "api_integration": "clawcross-search-agent",
        }
        payload.update(self.extra_payload)
        return payload


def load_targets(targets_path: Path, selected_sites: set[str] | None = None) -> list[Target]:
    if not targets_path.exists():
        raise FileNotFoundError(
            f"Targets file not found: {targets_path}. Create it from {EXAMPLE_TARGETS_PATH} first."
        )

    raw = json.loads(targets_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        defaults: dict[str, Any] = {}
        items = raw
    elif isinstance(raw, dict):
        defaults = raw.get("defaults") or {}
        items = raw.get("targets") or []
    else:
        raise ValueError("Targets file must be a JSON array or an object with a 'targets' array.")

    targets: list[Target] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"Target entry #{index + 1} must be an object.")
        merged = {**defaults, **item}
        name = str(merged.get("name") or merged.get("site_key") or f"target-{index + 1}").strip()
        url = str(merged.get("url") or "").strip()
        if not url:
            raise ValueError(f"Target '{name}' is missing a url.")
        site_key = str(merged.get("site_key") or canonical_slug(name)).strip()
        if selected_sites and site_key not in selected_sites and name not in selected_sites:
            continue
        goal = str(merged.get("goal") or build_default_goal(name, url)).strip()
        browser_profile = str(merged.get("browser_profile") or "lite").strip().lower()
        extra_payload = dict(merged.get("extra_payload") or {})
        targets.append(
            Target(
                site_key=site_key,
                name=name,
                url=url,
                goal=goal,
                browser_profile=browser_profile,
                extra_payload=extra_payload,
            )
        )

    if not targets:
        raise ValueError("No targets matched the provided filters.")
    if len(targets) > 100:
        raise ValueError("TinyFish batch API accepts at most 100 runs per request.")
    return targets


class TinyFishClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw) if raw else {"error": exc.reason}
            except json.JSONDecodeError:
                body = {"error": raw or exc.reason}
            raise RuntimeError(f"TinyFish API {exc.code} on {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach TinyFish on {path}: {exc.reason}") from exc

    def start_batch(self, targets: list[Target]) -> list[str]:
        body = self._post("/v1/automation/run-batch", {"runs": [target.to_payload() for target in targets]})
        run_ids = body.get("run_ids") or []
        if len(run_ids) != len(targets):
            raise RuntimeError(f"TinyFish returned {len(run_ids)} run_ids for {len(targets)} targets: {body}")
        return [str(run_id) for run_id in run_ids]

    def get_runs_batch(self, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        body = self._post("/v1/runs/batch", {"run_ids": run_ids})
        return list(body.get("data") or [])

    def probe(self) -> dict[str, Any]:
        probe_id = f"clawcross-probe-{int(time.time())}"
        return self._post("/v1/runs/batch", {"run_ids": [probe_id]})

    def run_sse(self, target: Target) -> Iterator[dict[str, Any]]:
        request = urllib.request.Request(
            f"{self.base_url}/v1/automation/run-sse",
            data=json.dumps(target.to_payload()).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                yield from iter_sse_json_events(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw) if raw else {"error": exc.reason}
            except json.JSONDecodeError:
                body = {"error": raw or exc.reason}
            raise RuntimeError(f"TinyFish API {exc.code} on /v1/automation/run-sse: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Failed to reach TinyFish on /v1/automation/run-sse: {exc.reason}") from exc


def _decode_sse_event(event_name: str | None, data_lines: list[str]) -> dict[str, Any] | None:
    payload = "\n".join(data_lines).strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        parsed = {"raw": payload}

    if isinstance(parsed, dict):
        event = dict(parsed)
    else:
        event = {"data": parsed}

    if event_name:
        event.setdefault("type", event_name)
        event["_sse_event"] = event_name
    return event


def iter_sse_json_events(lines: Iterable[Any]) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    event_name: str | None = None

    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            event = _decode_sse_event(event_name, data_lines)
            if event is not None:
                yield event
            data_lines = []
            event_name = None
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event_name = value or event_name
        elif field == "data":
            data_lines.append(value)

    event = _decode_sse_event(event_name, data_lines)
    if event is not None:
        yield event


class MonitorDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tinyfish_runs (
                run_id TEXT PRIMARY KEY,
                site_key TEXT NOT NULL,
                site_name TEXT NOT NULL,
                url TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                browser_profile TEXT,
                submitted_at TEXT NOT NULL,
                created_at TEXT,
                started_at TEXT,
                finished_at TEXT,
                error_json TEXT,
                raw_result_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tinyfish_runs_status
            ON tinyfish_runs(status);

            CREATE INDEX IF NOT EXISTS idx_tinyfish_runs_site_finished
            ON tinyfish_runs(site_key, finished_at);

            CREATE TABLE IF NOT EXISTS tinyfish_price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                site_key TEXT NOT NULL,
                site_name TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_name TEXT,
                plan_name TEXT,
                sku TEXT,
                currency TEXT,
                amount_text TEXT,
                amount_value REAL,
                billing_period TEXT,
                availability TEXT,
                source_url TEXT,
                captured_at TEXT NOT NULL,
                raw_item_json TEXT NOT NULL,
                price_hash TEXT NOT NULL,
                UNIQUE(run_id, item_key),
                FOREIGN KEY (run_id) REFERENCES tinyfish_runs(run_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tinyfish_snapshots_site_item
            ON tinyfish_price_snapshots(site_key, item_key, captured_at);

            CREATE TABLE IF NOT EXISTS tinyfish_price_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                site_key TEXT NOT NULL,
                item_key TEXT NOT NULL,
                change_type TEXT NOT NULL,
                snapshot_id INTEGER,
                previous_snapshot_id INTEGER,
                previous_amount_text TEXT,
                current_amount_text TEXT,
                previous_billing_period TEXT,
                current_billing_period TEXT,
                previous_availability TEXT,
                current_availability TEXT,
                details_json TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES tinyfish_runs(run_id) ON DELETE CASCADE
            );
            """
        )
        self.conn.commit()

    def record_submissions(self, targets: list[Target], run_ids: list[str]) -> None:
        submitted_at = utc_now()
        with self.conn:
            for target, run_id in zip(targets, run_ids):
                self.conn.execute(
                    """
                    INSERT INTO tinyfish_runs (
                        run_id, site_key, site_name, url, goal, status, browser_profile,
                        submitted_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        site_key=excluded.site_key,
                        site_name=excluded.site_name,
                        url=excluded.url,
                        goal=excluded.goal,
                        status=excluded.status,
                        browser_profile=excluded.browser_profile,
                        updated_at=excluded.updated_at
                    """,
                    (
                        run_id,
                        target.site_key,
                        target.name,
                        target.url,
                        target.goal,
                        "QUEUED",
                        target.browser_profile,
                        submitted_at,
                        submitted_at,
                    ),
                )

    def load_pending_runs(self, selected_sites: set[str] | None = None) -> list[tuple[dict[str, Any], Target]]:
        query = """
            SELECT run_id, site_key, site_name, url, goal, status, browser_profile
            FROM tinyfish_runs
            WHERE status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
            ORDER BY submitted_at ASC
        """
        rows = self.conn.execute(query).fetchall()
        results: list[tuple[dict[str, Any], Target]] = []
        for row in rows:
            site_key = row["site_key"]
            site_name = row["site_name"]
            if selected_sites and site_key not in selected_sites and site_name not in selected_sites:
                continue
            run_stub = {"run_id": row["run_id"], "status": row["status"]}
            target = Target(
                site_key=site_key,
                name=site_name,
                url=row["url"],
                goal=row["goal"],
                browser_profile=row["browser_profile"] or "lite",
            )
            results.append((run_stub, target))
        return results

    def _snapshot_row_for_item(self, target: Target, run: dict[str, Any], item: dict[str, Any], used_keys: set[str], index: int) -> dict[str, Any]:
        raw_name = first_present(item, "name", "title", "product", "product_name", "plan_name", "plan", "tier")
        plan_name = first_present(item, "plan_name", "plan", "tier", "package")
        sku = first_present(item, "sku", "id", "product_id", "plan_id")
        price_text = first_present(
            item,
            "price",
            "price_text",
            "amount_text",
            "display_price",
            "starting_price",
            "monthly_price",
            "annual_price",
        )
        amount_value = first_present(item, "amount", "price_amount", "value", "numeric_price")
        source_url = first_present(item, "source_url", "url", "pricing_url") or target.url
        amount_number = coerce_float(amount_value if amount_value not in (None, "") else price_text)
        currency = detect_currency(item, str(price_text) if price_text is not None else None)
        billing_period = detect_billing_period(item, str(price_text) if price_text is not None else None)
        availability = detect_availability(item)
        item_name = str(raw_name or plan_name or sku or target.name).strip()
        base_key = str(first_present(item, "item_key") or sku or plan_name or raw_name or f"{target.site_key}-{index + 1}").strip()
        item_key = canonical_slug(base_key)
        while item_key in used_keys:
            item_key = f"{item_key}-{index + 1}"
        used_keys.add(item_key)

        snapshot = {
            "run_id": run["run_id"],
            "site_key": target.site_key,
            "site_name": target.name,
            "item_key": item_key,
            "item_name": item_name,
            "plan_name": str(plan_name).strip() if plan_name not in (None, "") else None,
            "sku": str(sku).strip() if sku not in (None, "") else None,
            "currency": currency,
            "amount_text": str(price_text).strip() if price_text not in (None, "") else None,
            "amount_value": amount_number,
            "billing_period": billing_period,
            "availability": availability,
            "source_url": str(source_url).strip(),
            "captured_at": run.get("finished_at") or run.get("started_at") or run.get("created_at") or utc_now(),
            "raw_item_json": stable_json(item),
        }
        hash_basis = {
            "item_name": snapshot["item_name"],
            "plan_name": snapshot["plan_name"],
            "sku": snapshot["sku"],
            "currency": snapshot["currency"],
            "amount_text": snapshot["amount_text"],
            "amount_value": snapshot["amount_value"],
            "billing_period": snapshot["billing_period"],
            "availability": snapshot["availability"],
            "source_url": snapshot["source_url"],
        }
        snapshot["price_hash"] = hashlib.sha256(stable_json(hash_basis).encode("utf-8")).hexdigest()
        return snapshot

    def _extract_items(self, result: Any) -> list[dict[str, Any]]:
        data = maybe_json(result)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in PRICE_LIST_KEYS:
                value = data.get(key)
                if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                    return list(value)
            for value in data.values():
                if isinstance(value, dict):
                    for key in PRICE_LIST_KEYS:
                        nested = value.get(key)
                        if isinstance(nested, list) and all(isinstance(item, dict) for item in nested):
                            return list(nested)
            return [data]
        return []

    def _persist_change(
        self,
        *,
        run_id: str,
        site_key: str,
        item_key: str,
        change_type: str,
        snapshot_id: int | None,
        previous_snapshot_id: int | None,
        previous_snapshot: sqlite3.Row | None,
        current_snapshot: dict[str, Any] | None,
        detected_at: str,
    ) -> None:
        details = {
            "site_key": site_key,
            "item_key": item_key,
            "change_type": change_type,
            "previous": {
                "amount_text": previous_snapshot["amount_text"] if previous_snapshot else None,
                "billing_period": previous_snapshot["billing_period"] if previous_snapshot else None,
                "availability": previous_snapshot["availability"] if previous_snapshot else None,
            },
            "current": {
                "amount_text": current_snapshot["amount_text"] if current_snapshot else None,
                "billing_period": current_snapshot["billing_period"] if current_snapshot else None,
                "availability": current_snapshot["availability"] if current_snapshot else None,
            },
        }
        self.conn.execute(
            """
            INSERT INTO tinyfish_price_changes (
                run_id, site_key, item_key, change_type, snapshot_id, previous_snapshot_id,
                previous_amount_text, current_amount_text,
                previous_billing_period, current_billing_period,
                previous_availability, current_availability,
                details_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                site_key,
                item_key,
                change_type,
                snapshot_id,
                previous_snapshot_id,
                previous_snapshot["amount_text"] if previous_snapshot else None,
                current_snapshot["amount_text"] if current_snapshot else None,
                previous_snapshot["billing_period"] if previous_snapshot else None,
                current_snapshot["billing_period"] if current_snapshot else None,
                previous_snapshot["availability"] if previous_snapshot else None,
                current_snapshot["availability"] if current_snapshot else None,
                stable_json(details),
                detected_at,
            ),
        )

    def persist_run_result(self, target: Target, run: dict[str, Any]) -> dict[str, Any]:
        status = str(run.get("status") or "UNKNOWN").upper()
        updated_at = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO tinyfish_runs (
                    run_id, site_key, site_name, url, goal, status, browser_profile,
                    submitted_at, created_at, started_at, finished_at,
                    error_json, raw_result_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    created_at=excluded.created_at,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at,
                    error_json=excluded.error_json,
                    raw_result_json=excluded.raw_result_json,
                    updated_at=excluded.updated_at
                """,
                (
                    run["run_id"],
                    target.site_key,
                    target.name,
                    target.url,
                    target.goal,
                    status,
                    target.browser_profile,
                    updated_at,
                    run.get("created_at"),
                    run.get("started_at"),
                    run.get("finished_at"),
                    stable_json(run.get("error")) if run.get("error") is not None else None,
                    stable_json(maybe_json(run.get("result"))) if run.get("result") is not None else None,
                    updated_at,
                ),
            )

            self.conn.execute("DELETE FROM tinyfish_price_changes WHERE run_id = ?", (run["run_id"],))
            self.conn.execute("DELETE FROM tinyfish_price_snapshots WHERE run_id = ?", (run["run_id"],))

            result = maybe_json(run.get("result"))
            items = self._extract_items(result) if status == "COMPLETED" else []
            used_keys: set[str] = set()
            current_snapshots: dict[str, dict[str, Any]] = {}
            current_snapshot_ids: dict[str, int] = {}

            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                snapshot = self._snapshot_row_for_item(target, run, item, used_keys, index)
                cursor = self.conn.execute(
                    """
                    INSERT INTO tinyfish_price_snapshots (
                        run_id, site_key, site_name, item_key, item_name, plan_name, sku,
                        currency, amount_text, amount_value, billing_period, availability,
                        source_url, captured_at, raw_item_json, price_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot["run_id"],
                        snapshot["site_key"],
                        snapshot["site_name"],
                        snapshot["item_key"],
                        snapshot["item_name"],
                        snapshot["plan_name"],
                        snapshot["sku"],
                        snapshot["currency"],
                        snapshot["amount_text"],
                        snapshot["amount_value"],
                        snapshot["billing_period"],
                        snapshot["availability"],
                        snapshot["source_url"],
                        snapshot["captured_at"],
                        snapshot["raw_item_json"],
                        snapshot["price_hash"],
                    ),
                )
                current_snapshots[snapshot["item_key"]] = snapshot
                current_snapshot_ids[snapshot["item_key"]] = int(cursor.lastrowid)

            previous_run = self.conn.execute(
                """
                SELECT run_id
                FROM tinyfish_runs
                WHERE site_key = ?
                  AND status = 'COMPLETED'
                  AND run_id != ?
                  AND COALESCE(finished_at, started_at, created_at, submitted_at) < COALESCE(?, ?)
                ORDER BY COALESCE(finished_at, started_at, created_at, submitted_at) DESC
                LIMIT 1
                """,
                (
                    target.site_key,
                    run["run_id"],
                    run.get("finished_at"),
                    updated_at,
                ),
            ).fetchone()

            previous_snapshots: dict[str, sqlite3.Row] = {}
            if previous_run:
                rows = self.conn.execute(
                    """
                    SELECT id, item_key, amount_text, billing_period, availability, price_hash
                    FROM tinyfish_price_snapshots
                    WHERE run_id = ?
                    """,
                    (previous_run["run_id"],),
                ).fetchall()
                previous_snapshots = {row["item_key"]: row for row in rows}

            detected_at = updated_at
            change_count = 0

            for item_key, snapshot in current_snapshots.items():
                previous_snapshot = previous_snapshots.get(item_key)
                if previous_snapshot is None:
                    if previous_run:
                        self._persist_change(
                            run_id=run["run_id"],
                            site_key=target.site_key,
                            item_key=item_key,
                            change_type="NEW",
                            snapshot_id=current_snapshot_ids[item_key],
                            previous_snapshot_id=None,
                            previous_snapshot=None,
                            current_snapshot=snapshot,
                            detected_at=detected_at,
                        )
                        change_count += 1
                    continue

                if previous_snapshot["price_hash"] != snapshot["price_hash"]:
                    self._persist_change(
                        run_id=run["run_id"],
                        site_key=target.site_key,
                        item_key=item_key,
                        change_type="UPDATED",
                        snapshot_id=current_snapshot_ids[item_key],
                        previous_snapshot_id=int(previous_snapshot["id"]),
                        previous_snapshot=previous_snapshot,
                        current_snapshot=snapshot,
                        detected_at=detected_at,
                    )
                    change_count += 1

            if previous_snapshots:
                missing_keys = set(previous_snapshots) - set(current_snapshots)
                for item_key in sorted(missing_keys):
                    previous_snapshot = previous_snapshots[item_key]
                    self._persist_change(
                        run_id=run["run_id"],
                        site_key=target.site_key,
                        item_key=item_key,
                        change_type="REMOVED",
                        snapshot_id=None,
                        previous_snapshot_id=int(previous_snapshot["id"]),
                        previous_snapshot=previous_snapshot,
                        current_snapshot=None,
                        detected_at=detected_at,
                    )
                    change_count += 1

        return {
            "run_id": run["run_id"],
            "site_key": target.site_key,
            "site_name": target.name,
            "status": status,
            "snapshot_count": len(current_snapshots),
            "change_count": change_count,
        }

    def list_recent_changes(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT run_id, site_key, item_key, change_type, previous_amount_text, current_amount_text, detected_at
            FROM tinyfish_price_changes
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def count_pending_runs(self) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM tinyfish_runs
            WHERE status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')
            """
        ).fetchone()
        return int(row[0] if row else 0)

    def list_recent_runs(self, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT run_id, site_key, site_name, status, submitted_at, created_at, started_at, finished_at
            FROM tinyfish_runs
            ORDER BY COALESCE(finished_at, started_at, created_at, submitted_at) DESC, updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def list_latest_site_runs(self, limit: int | None = None) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            """
            WITH ranked AS (
                SELECT
                    run_id,
                    site_key,
                    site_name,
                    status,
                    COALESCE(finished_at, started_at, created_at, submitted_at) AS captured_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY site_key
                        ORDER BY COALESCE(finished_at, started_at, created_at, submitted_at) DESC
                    ) AS rn
                FROM tinyfish_runs
                WHERE status = 'COMPLETED'
            )
            SELECT run_id, site_key, site_name, status, captured_at
            FROM ranked
            WHERE rn = 1
            ORDER BY captured_at DESC
            """
        ).fetchall()
        if limit is None:
            return rows
        return rows[:limit]

    def list_snapshots_for_run(self, run_id: str, limit: int | None = None) -> list[sqlite3.Row]:
        query = """
            SELECT
                id,
                run_id,
                site_key,
                site_name,
                item_key,
                item_name,
                plan_name,
                sku,
                currency,
                amount_text,
                amount_value,
                billing_period,
                availability,
                source_url,
                captured_at
            FROM tinyfish_price_snapshots
            WHERE run_id = ?
            ORDER BY item_name COLLATE NOCASE ASC, item_key ASC
        """
        params: list[Any] = [run_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(query, params).fetchall()

    def list_latest_site_snapshots(self, limit_sites: int = 10, per_site_limit: int = 20) -> list[dict[str, Any]]:
        sites: list[dict[str, Any]] = []
        for row in self.list_latest_site_runs(limit_sites):
            snapshots = [
                {
                    "id": snap["id"],
                    "item_key": snap["item_key"],
                    "item_name": snap["item_name"],
                    "plan_name": snap["plan_name"],
                    "sku": snap["sku"],
                    "currency": snap["currency"],
                    "amount_text": snap["amount_text"],
                    "amount_value": snap["amount_value"],
                    "billing_period": snap["billing_period"],
                    "availability": snap["availability"],
                    "source_url": snap["source_url"],
                    "captured_at": snap["captured_at"],
                }
                for snap in self.list_snapshots_for_run(row["run_id"], per_site_limit)
            ]
            sites.append(
                {
                    "run_id": row["run_id"],
                    "site_key": row["site_key"],
                    "site_name": row["site_name"],
                    "captured_at": row["captured_at"],
                    "status": row["status"],
                    "snapshot_count": len(snapshots),
                    "snapshots": snapshots,
                }
            )
        return sites


def poll_runs(
    client: TinyFishClient,
    db: MonitorDB,
    run_targets: list[tuple[str, Target]],
    poll_interval: float,
    max_wait_seconds: int,
) -> list[dict[str, Any]]:
    started_at = time.time()
    pending: dict[str, Target] = {run_id: target for run_id, target in run_targets}
    completed: list[dict[str, Any]] = []

    while pending:
        if max_wait_seconds > 0 and time.time() - started_at > max_wait_seconds:
            raise TimeoutError(f"Timed out waiting for {len(pending)} TinyFish runs.")

        runs = client.get_runs_batch(list(pending.keys()))
        seen_run_ids = set()
        for run in runs:
            run_id = str(run.get("run_id") or "")
            if not run_id or run_id not in pending:
                continue
            seen_run_ids.add(run_id)
            status = str(run.get("status") or "").upper()
            summary = db.persist_run_result(pending[run_id], run)
            if status in FINAL_STATUSES:
                completed.append(summary)
                pending.pop(run_id, None)

        missing = set(pending) - seen_run_ids
        if missing:
            print(
                f"Warning: TinyFish batch status response omitted {len(missing)} run(s): {', '.join(sorted(missing))}",
                file=sys.stderr,
            )

        if pending:
            time.sleep(poll_interval)

    return completed


def format_summary(results: list[dict[str, Any]]) -> str:
    lines = []
    for result in results:
        lines.append(
            f"- {result['site_name']} [{result['status']}] "
            f"snapshots={result['snapshot_count']} changes={result['change_count']} run_id={result['run_id']}"
        )
    return "\n".join(lines)


def serialize_targets(targets: list[Target]) -> list[dict[str, Any]]:
    return [
        {
            "site_key": target.site_key,
            "site_name": target.name,
            "url": target.url,
            "browser_profile": target.browser_profile,
        }
        for target in targets
    ]


def list_configured_targets(targets_path: str | Path | None = None) -> list[dict[str, Any]]:
    return serialize_targets(load_targets(get_targets_path(targets_path)))


def list_configured_targets_safe(targets_path: str | Path | None = None) -> tuple[list[dict[str, Any]], str | None]:
    try:
        return list_configured_targets(targets_path), None
    except FileNotFoundError:
        return [], None
    except Exception as exc:
        return [], str(exc)


def load_target_by_site_key(
    site_key: str,
    *,
    targets_path: str | Path | None = None,
) -> Target:
    normalized = site_key.strip()
    if not normalized:
        raise ValueError("site_key is required.")
    matches = load_targets(get_targets_path(targets_path), {normalized})
    if not matches:
        raise ValueError(f"Target not found: {normalized}")
    return matches[0]


def get_api_key() -> str:
    return os.getenv("TINYFISH_API_KEY", "").strip()


def get_base_url() -> str:
    return os.getenv("TINYFISH_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL


def get_db_path(db_path: str | Path | None = None) -> Path:
    explicit = db_path if db_path is not None else os.getenv("TINYFISH_MONITOR_DB_PATH", str(DEFAULT_DB_PATH))
    return Path(explicit)


def get_targets_path(targets_path: str | Path | None = None) -> Path:
    explicit = targets_path if targets_path is not None else os.getenv("TINYFISH_MONITOR_TARGETS_PATH", str(DEFAULT_TARGETS_PATH))
    return Path(explicit)


def create_client(request_timeout: int = DEFAULT_REQUEST_TIMEOUT) -> TinyFishClient:
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("TINYFISH_API_KEY is not configured.")
    return TinyFishClient(
        api_key=api_key,
        base_url=get_base_url(),
        timeout_seconds=request_timeout,
    )


def probe_api_access(
    api_key: str,
    base_url: str | None = None,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    normalized_key = (api_key or "").strip()
    if not normalized_key:
        raise RuntimeError("TINYFISH_API_KEY is not configured.")
    client = TinyFishClient(
        api_key=normalized_key,
        base_url=(base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        timeout_seconds=request_timeout,
    )
    return client.probe()


def get_monitor_config() -> dict[str, Any]:
    targets_path = get_targets_path()
    db_path = get_db_path()
    cron_expr = os.getenv("TINYFISH_MONITOR_CRON", "").strip()
    api_key = get_api_key()
    return {
        "api_key_configured": bool(api_key),
        "base_url": get_base_url(),
        "targets_path": str(targets_path),
        "targets_path_exists": targets_path.exists(),
        "db_path": str(db_path),
        "db_path_exists": db_path.exists(),
        "enabled": env_flag("TINYFISH_MONITOR_ENABLED", False),
        "cron": cron_expr,
        "schedule_configured": bool(cron_expr),
    }


def submit_monitor_run(
    *,
    targets_path: str | Path | None = None,
    db_path: str | Path | None = None,
    selected_sites: set[str] | None = None,
    wait: bool = False,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    targets = load_targets(get_targets_path(targets_path), selected_sites)
    client = create_client(request_timeout)
    db = MonitorDB(get_db_path(db_path))
    try:
        run_ids = client.start_batch(targets)
        db.record_submissions(targets, run_ids)
        response: dict[str, Any] = {
            "submitted": len(run_ids),
            "run_ids": run_ids,
            "targets": serialize_targets(targets),
            "waited": wait,
        }
        if wait:
            response["results"] = poll_runs(
                client=client,
                db=db,
                run_targets=list(zip(run_ids, targets)),
                poll_interval=poll_interval,
                max_wait_seconds=max_wait_seconds,
            )
        return response
    finally:
        db.close()


def poll_pending_runs(
    *,
    db_path: str | Path | None = None,
    selected_sites: set[str] | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    db = MonitorDB(get_db_path(db_path))
    try:
        pending_pairs = db.load_pending_runs(selected_sites)
        if not pending_pairs:
            return {"pending": 0, "results": []}
        client = create_client(request_timeout)
        results = poll_runs(
            client=client,
            db=db,
            run_targets=[(run["run_id"], target) for run, target in pending_pairs],
            poll_interval=poll_interval,
            max_wait_seconds=max_wait_seconds,
        )
        return {"pending": len(pending_pairs), "results": results}
    finally:
        db.close()


def poll_pending_runs_once(
    *,
    db_path: str | Path | None = None,
    selected_sites: set[str] | None = None,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> dict[str, Any]:
    db = MonitorDB(get_db_path(db_path))
    try:
        pending_pairs = db.load_pending_runs(selected_sites)
        if not pending_pairs:
            return {"pending": 0, "completed": [], "still_pending": []}

        client = create_client(request_timeout)
        pending_map = {run["run_id"]: target for run, target in pending_pairs}
        runs = client.get_runs_batch(list(pending_map))
        completed: list[dict[str, Any]] = []
        still_pending: list[str] = []

        for run in runs:
            run_id = str(run.get("run_id") or "")
            if not run_id or run_id not in pending_map:
                continue
            summary = db.persist_run_result(pending_map[run_id], run)
            if summary["status"] in FINAL_STATUSES:
                completed.append(summary)
            else:
                still_pending.append(run_id)

        known = {str(run.get("run_id") or "") for run in runs}
        for run_id in pending_map:
            if run_id not in known and run_id not in still_pending:
                still_pending.append(run_id)

        return {
            "pending": len(pending_pairs),
            "completed": completed,
            "still_pending": still_pending,
        }
    finally:
        db.close()


def get_monitor_overview(
    *,
    db_path: str | Path | None = None,
    recent_change_limit: int = 20,
    recent_run_limit: int = 10,
    latest_site_limit: int = 10,
    snapshots_per_site: int = 20,
) -> dict[str, Any]:
    db = MonitorDB(get_db_path(db_path))
    targets, targets_error = list_configured_targets_safe()
    try:
        return {
            "config": get_monitor_config(),
            "targets": targets,
            "targets_error": targets_error,
            "pending_runs": db.count_pending_runs(),
            "recent_runs": [
                {
                    "run_id": row["run_id"],
                    "site_key": row["site_key"],
                    "site_name": row["site_name"],
                    "status": row["status"],
                    "submitted_at": row["submitted_at"],
                    "created_at": row["created_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                }
                for row in db.list_recent_runs(recent_run_limit)
            ],
            "recent_changes": [
                {
                    "run_id": row["run_id"],
                    "site_key": row["site_key"],
                    "item_key": row["item_key"],
                    "change_type": row["change_type"],
                    "previous_amount_text": row["previous_amount_text"],
                    "current_amount_text": row["current_amount_text"],
                    "detected_at": row["detected_at"],
                }
                for row in db.list_recent_changes(recent_change_limit)
            ],
            "sites": db.list_latest_site_snapshots(latest_site_limit, snapshots_per_site),
        }
    finally:
        db.close()


def get_latest_site_snapshots(
    site_key: str,
    *,
    db_path: str | Path | None = None,
    snapshots_limit: int = 50,
) -> dict[str, Any]:
    db = MonitorDB(get_db_path(db_path))
    try:
        for site in db.list_latest_site_snapshots(limit_sites=1000, per_site_limit=snapshots_limit):
            if site["site_key"] == site_key:
                return site
        return {"site_key": site_key, "snapshots": []}
    finally:
        db.close()


def stream_live_run(
    *,
    site_key: str,
    targets_path: str | Path | None = None,
    db_path: str | Path | None = None,
    request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> Iterator[dict[str, Any]]:
    target = load_target_by_site_key(site_key, targets_path=targets_path)
    client = create_client(request_timeout)
    db = MonitorDB(get_db_path(db_path))
    submitted_run_id: str | None = None
    started_at: str | None = None

    try:
        for event in client.run_sse(target):
            event_type = str(event.get("type") or "").upper()
            run_id = str(event.get("run_id") or submitted_run_id or "").strip() or None

            if run_id and submitted_run_id is None:
                submitted_run_id = run_id
                db.record_submissions([target], [run_id])

            if event_type == "STARTED":
                started_at = str(event.get("timestamp") or event.get("started_at") or utc_now())

            if event_type == "COMPLETE" and submitted_run_id:
                run_record = {
                    "run_id": submitted_run_id,
                    "status": str(event.get("status") or "COMPLETED").upper(),
                    "created_at": event.get("created_at") or started_at,
                    "started_at": event.get("started_at") or started_at,
                    "finished_at": event.get("finished_at") or event.get("timestamp") or utc_now(),
                    "error": event.get("error") or event.get("errors"),
                    "result": event.get("result"),
                }
                summary = db.persist_run_result(target, run_record)
                event = {**event, "clawcross_summary": summary}

            yield event
    except Exception as exc:
        if submitted_run_id:
            db.persist_run_result(
                target,
                {
                    "run_id": submitted_run_id,
                    "status": "FAILED",
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "error": {"message": str(exc)},
                    "result": {},
                },
            )
        raise
    finally:
        db.close()


def run_scheduled_monitor_job() -> dict[str, Any]:
    return submit_monitor_run(wait=True)


def cmd_run(args: argparse.Namespace) -> int:
    api_key = os.getenv("TINYFISH_API_KEY", "").strip()
    if not api_key:
        print("TINYFISH_API_KEY is not set. Add it to your shell or config/.env.", file=sys.stderr)
        return 2

    selected_sites = set(args.site or [])
    targets = load_targets(Path(args.targets), selected_sites or None)
    client = TinyFishClient(
        api_key=api_key,
        base_url=os.getenv("TINYFISH_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        timeout_seconds=args.request_timeout,
    )
    db = MonitorDB(Path(args.db))
    try:
        run_ids = client.start_batch(targets)
        db.record_submissions(targets, run_ids)
        for target, run_id in zip(targets, run_ids):
            print(f"submitted {target.site_key}: {run_id}")

        if args.no_wait:
            print("Runs submitted. Use the 'poll' command later to persist completed results.")
            return 0

        results = poll_runs(
            client=client,
            db=db,
            run_targets=list(zip(run_ids, targets)),
            poll_interval=args.poll_interval,
            max_wait_seconds=args.max_wait,
        )
        print(format_summary(results))
        return 0 if all(result["status"] == "COMPLETED" for result in results) else 1
    finally:
        db.close()


def cmd_poll(args: argparse.Namespace) -> int:
    api_key = os.getenv("TINYFISH_API_KEY", "").strip()
    if not api_key:
        print("TINYFISH_API_KEY is not set. Add it to your shell or config/.env.", file=sys.stderr)
        return 2

    db = MonitorDB(Path(args.db))
    try:
        pending_pairs = db.load_pending_runs(set(args.site or []) or None)
        if not pending_pairs:
            print("No pending TinyFish runs found in the database.")
            return 0

        client = TinyFishClient(
            api_key=api_key,
            base_url=os.getenv("TINYFISH_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            timeout_seconds=args.request_timeout,
        )
        results = poll_runs(
            client=client,
            db=db,
            run_targets=[(run["run_id"], target) for run, target in pending_pairs],
            poll_interval=args.poll_interval,
            max_wait_seconds=args.max_wait,
        )
        print(format_summary(results))
        return 0 if all(result["status"] == "COMPLETED" for result in results) else 1
    finally:
        db.close()


def cmd_report(args: argparse.Namespace) -> int:
    db = MonitorDB(Path(args.db))
    try:
        rows = db.list_recent_changes(args.limit)
        if not rows:
            print("No pricing changes recorded yet.")
            return 0
        for row in rows:
            print(
                f"{row['detected_at']} {row['site_key']} {row['change_type']} {row['item_key']} "
                f"{row['previous_amount_text'] or '-'} -> {row['current_amount_text'] or '-'} "
                f"(run {row['run_id']})"
            )
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TinyFish internet search agent for Clawcross")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Submit targets to TinyFish, wait, and store results")
    run_parser.add_argument("--targets", default=str(get_targets_path()), help="Path to search target JSON")
    run_parser.add_argument("--db", default=str(get_db_path()), help="SQLite path for TinyFish monitor data")
    run_parser.add_argument("--site", action="append", help="Filter by site_key or target name (repeatable)")
    run_parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between polling requests")
    run_parser.add_argument("--max-wait", type=int, default=900, help="Maximum seconds to wait for completion")
    run_parser.add_argument("--request-timeout", type=int, default=60, help="HTTP timeout in seconds")
    run_parser.add_argument("--no-wait", action="store_true", help="Submit runs only and skip polling")
    run_parser.set_defaults(func=cmd_run)

    poll_parser = subparsers.add_parser("poll", help="Poll pending TinyFish runs already stored in the DB")
    poll_parser.add_argument("--db", default=str(get_db_path()), help="SQLite path for TinyFish monitor data")
    poll_parser.add_argument("--site", action="append", help="Filter by site_key or target name (repeatable)")
    poll_parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between polling requests")
    poll_parser.add_argument("--max-wait", type=int, default=900, help="Maximum seconds to wait for completion")
    poll_parser.add_argument("--request-timeout", type=int, default=60, help="HTTP timeout in seconds")
    poll_parser.set_defaults(func=cmd_poll)

    report_parser = subparsers.add_parser("report", help="Show recently detected data changes")
    report_parser.add_argument("--db", default=str(get_db_path()), help="SQLite path for TinyFish monitor data")
    report_parser.add_argument("--limit", type=int, default=20, help="How many recent change rows to print")
    report_parser.set_defaults(func=cmd_report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
