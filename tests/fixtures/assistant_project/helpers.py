"""Small functions with a branch, a cross-module call and a write path."""


def normalize_record(raw):
    return raw.strip().lower()


def resolve_conflict(value):
    return value + "-copy"


def persist_record(destination, value):
    with open(destination, "w", encoding="utf-8") as stream:
        stream.write(value)
