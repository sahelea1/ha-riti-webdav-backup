"""
RitiBackup retention engine.

Default policy (matches the requested scheme for every-second-day backups):

  * Keep the `keep_recent` newest backups (default 4 -> covers ~8 days at a
    2-day cadence).
  * Keep one "archive" backup closest to `archive_age_days` old (default 14,
    i.e. roughly two weeks ago).
  * Delete everything else.

At steady state that leaves 5 backups remotely: four recent plus one fortnight
archive.

A note on drift: with `keep_bridge` disabled, the intermediate backups between
the recent window and the archive are pruned, so over many cycles the single
archive slowly ages past exactly 14 days (there is nothing left to "promote"
into the slot). If you want the fortnight archive to stay fresh, enable
`keep_bridge`: it retains the chain of backups between the recent window and the
archive target so a backup is always ready to become the next archive. That
keeps the archive at ~14 days at the cost of holding a few more backups
(~8 total at a 2-day cadence).

This module is pure logic over (id, timestamp) entries so it is trivially
testable and independent of WebDAV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, List, Sequence


@dataclass(frozen=True)
class BackupEntry:
    id: str            # remote filename / identifier
    timestamp: datetime  # creation time (tz-aware UTC)
    size: int = 0


@dataclass
class RetentionPlan:
    keep: List[BackupEntry] = field(default_factory=list)
    delete: List[BackupEntry] = field(default_factory=list)
    reasons: dict = field(default_factory=dict)  # id -> human reason for keeping


def plan_retention(
    entries: Iterable[BackupEntry],
    now: datetime,
    *,
    keep_recent: int = 4,
    archive_age_days: Sequence[int] | int = 14,
    keep_bridge: bool = False,
) -> RetentionPlan:
    """Decide which backups to keep and which to delete.

    Args:
        entries: all known remote backups.
        now: reference time (tz-aware UTC).
        keep_recent: how many newest backups to always retain.
        archive_age_days: target age(s) in days for archive slot(s). A single int
            or a list of ints (one archive kept per target).
        keep_bridge: keep the chain between the recent window and the oldest
            archive target so the archive slot self-refreshes over time.
    """
    items = sorted(entries, key=lambda e: e.timestamp, reverse=True)
    targets = [archive_age_days] if isinstance(archive_age_days, int) else list(archive_age_days)
    targets = sorted(set(t for t in targets if t > 0))

    keep_ids: dict[str, str] = {}

    # 1) Recent tier
    recent = items[:keep_recent]
    for i, e in enumerate(recent):
        keep_ids[e.id] = f"recent #{i + 1} of {keep_recent}"

    older = items[keep_recent:]

    # 2) Archive tier: closest backup to each target age (drawn from the older set)
    for t in targets:
        target_time = now - timedelta(days=t)
        candidates = [e for e in older if e.id not in keep_ids]
        if not candidates:
            continue
        archive = min(
            candidates,
            key=lambda e: (abs((e.timestamp - target_time).total_seconds()), -e.timestamp.timestamp()),
        )
        keep_ids[archive.id] = f"archive (~{t}d)"

    # 3) Optional bridge: keep everything between the recent window and the
    #    oldest archive target so a fresh archive is always available.
    if keep_bridge and targets:
        max_target = max(targets)
        cutoff = now - timedelta(days=max_target)
        for e in older:
            if e.id in keep_ids:
                continue
            if e.timestamp >= cutoff:
                keep_ids[e.id] = "bridge (pre-archive)"

    keep = [e for e in items if e.id in keep_ids]
    delete = [e for e in items if e.id not in keep_ids]
    return RetentionPlan(keep=keep, delete=delete, reasons=keep_ids)
