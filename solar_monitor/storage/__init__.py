"""Persistence layer.

Stores poll results in SQLite (WAL mode) with a long-format `samples` table
plus a `latest` snapshot for fast current-state queries. Rollup tables will
be added when query performance demands it; for now SQL-side aggregation on
the raw samples handles every range we care about at expected data volumes.
"""
from .sqlite import Store

__all__ = ["Store"]
