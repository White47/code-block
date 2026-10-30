"""Ordinary CLI-style path. Static analysis must never import this module."""

from helpers import normalize_record, persist_record, resolve_conflict

raise RuntimeError("Analyzed project code must not execute")


def process_record(raw, existing, destination):
    normalized = normalize_record(raw)
    if not normalized:
        return "empty"
    if normalized in existing:
        normalized = resolve_conflict(normalized)
    persist_record(destination, normalized)
    return normalized
