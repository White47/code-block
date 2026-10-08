"""Seed fresh, repository-local test metadata for a frozen native acceptance run."""

import argparse
from pathlib import Path

from codeviz.persistence.store import CacheStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-data", type=Path, required=True)
    parser.add_argument("--project", type=Path, action="append", required=True)
    args = parser.parse_args()
    build = Path(__file__).resolve().parents[1] / "build"
    app_data = args.app_data.resolve()
    cache = app_data / "codeviz"
    if not app_data.is_relative_to(build.resolve()) or cache.exists():
        parser.error("test app-data must be under build/ and have no existing codeviz cache")
    projects = [project.resolve(strict=True) for project in args.project]
    if not all(project.is_dir() for project in projects):
        parser.error("projects must be existing directories")
    store = CacheStore(cache)
    for project in projects:
        store.record_recent_project(project)
        store.save_annotations(project, {"architecture-test-note": "Retain during detail analysis"})
    print(f"Seeded only isolated recent-project metadata and annotations: {cache}")


if __name__ == "__main__":
    main()
