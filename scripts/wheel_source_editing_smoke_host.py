"""Verify and run an installed wheel, without importing the source checkout."""

import argparse
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9344)
    args = parser.parse_args()
    installed = args.installed.resolve(strict=True)
    sys.path.insert(0, str(installed))
    from codeviz.ui import desktop

    assert Path(desktop.__file__).resolve().is_relative_to(installed)
    assets = desktop.resolve_frontend_index()
    assert assets.is_relative_to(installed), "Wheel must use its own frontend assets"
    assert list((assets.parent / "assets").glob("editor.worker-*.js"))
    host = Path(__file__).with_name("source_editing_smoke_host.py")
    sys.argv = [str(host), "--port", str(args.port)]
    runpy.run_path(str(host), run_name="__main__")


if __name__ == "__main__":
    main()
