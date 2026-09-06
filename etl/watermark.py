"""Watermark storage.

Watermarks live in the app's own SQLite file, never in the source databases.
That is the same place ADF keeps a pipeline variable and Fivetran keeps its
cursor -- the source is not asked to remember anything about who is reading it.

One row per table per strategy, so switching a system from CT to CDC and back
does not have the two mechanisms overwriting each other's position.
"""
from __future__ import annotations

from core import registry
from core.models import Watermark


def read(system_key: str, spec: registry.TableSpec, strategy: str) -> dict:
    row = Watermark.objects.filter(system_key=system_key, source_table=spec.qualified,
                                   strategy=strategy).first()
    if row is None:
        return {}
    return {
        "ct_version": row.ct_version,
        "cdc_lsn": bytes.fromhex(row.cdc_lsn) if row.cdc_lsn else None,
        "high_water_ts": row.high_water_ts,
        "updated_at": row.updated_at,
    }


def write(system_key: str, spec: registry.TableSpec, strategy: str, *,
          ct_version=None, cdc_lsn=None, high_water_ts=None) -> None:
    if isinstance(cdc_lsn, (bytes, bytearray)):
        cdc_lsn = cdc_lsn.hex()
    Watermark.objects.update_or_create(
        system_key=system_key, source_table=spec.qualified, strategy=strategy,
        defaults={"ct_version": ct_version, "cdc_lsn": cdc_lsn,
                  "high_water_ts": high_water_ts},
    )


def clear(system_key: str, strategy: str | None = None) -> int:
    """Drop watermarks so the next load re-baselines.

    Called whenever a change mechanism is disabled. Changes made while a table
    was untracked are gone for good and SQL Server cannot warn you --
    CHANGE_TRACKING_MIN_VALID_VERSION only advances when the database version
    advances, so with every table untracked a stale watermark still looks valid.
    Deleting the watermark is the only safe response.
    """
    qs = Watermark.objects.filter(system_key=system_key)
    if strategy:
        qs = qs.filter(strategy=strategy)
    n, _ = qs.delete()
    return n


def all_rows() -> list[dict]:
    return [
        {"system_key": w.system_key, "source_table": w.source_table,
         "strategy": w.strategy, "ct_version": w.ct_version,
         "cdc_lsn": (w.cdc_lsn or "").upper() or None,
         "high_water_ts": w.high_water_ts.isoformat(sep=" ", timespec="seconds")
                          if w.high_water_ts else None,
         "updated_at": w.updated_at.isoformat(sep=" ", timespec="seconds")}
        for w in Watermark.objects.all()
    ]
