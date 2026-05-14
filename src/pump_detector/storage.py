from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

from .signals import SignalSnapshot


def _migrate_schema(conn: sqlite3.Connection, df: pd.DataFrame) -> None:
    """Add any columns present in *df* that are missing from the existing table."""
    cursor = conn.execute("PRAGMA table_info(signal_snapshots)")
    existing = {row[1] for row in cursor.fetchall()}
    for col in df.columns:
        if col not in existing:
            # Infer a SQLite affinity from the pandas dtype
            dtype = df[col].dtype
            if pd.api.types.is_integer_dtype(dtype):
                affinity = "INTEGER"
            elif pd.api.types.is_float_dtype(dtype):
                affinity = "REAL"
            elif pd.api.types.is_bool_dtype(dtype):
                affinity = "INTEGER"
            else:
                affinity = "TEXT"
            conn.execute(f'ALTER TABLE signal_snapshots ADD COLUMN "{col}" {affinity}')
    conn.commit()


def append_snapshots(snapshots: list[SignalSnapshot], sqlite_path: Path, alerts_csv: Path) -> None:
    if not snapshots:
        return
    rows = [snapshot.to_dict() for snapshot in snapshots]
    df = pd.DataFrame(rows)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    latest_csv = sqlite_path.parent / "latest_scan.csv"
    df.to_csv(latest_csv, index=False)
    try:
        with sqlite3.connect(sqlite_path, timeout=30) as conn:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA journal_mode = WAL")
            # Check whether the table already exists; if so, migrate missing columns.
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_snapshots'"
            ).fetchone()
            if table_exists:
                _migrate_schema(conn, df)
            df.to_sql("signal_snapshots", conn, if_exists="append", index=False)
    except sqlite3.Error as exc:
        if sqlite_path.exists() and sqlite_path.stat().st_size == 0:
            sqlite_path.unlink(missing_ok=True)
        warning_path = sqlite_path.parent / "storage_warnings.log"
        with warning_path.open("a", encoding="utf-8") as fh:
            fh.write(f"sqlite write skipped: {exc}\n")
    alerts = df[df["alert_triggered"] == True]  # noqa: E712 - pandas boolean filter
    if not alerts.empty:
        alerts_csv.parent.mkdir(parents=True, exist_ok=True)
        alerts.to_csv(alerts_csv, mode="a", header=not alerts_csv.exists(), index=False)


def read_recent_alerts(alerts_csv: Path) -> pd.DataFrame:
    if not alerts_csv.exists():
        return pd.DataFrame()
    return pd.read_csv(alerts_csv)
