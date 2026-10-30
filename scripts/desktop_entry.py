"""Frozen entry point and setuptools command for bundling built frontend assets."""

from pathlib import Path

if __name__ == "__main__":
    import sys

    if getattr(sys, "frozen", False) and sys.argv[1:2] == ["--_codeviz-assistant-offline-probe"]:
        from assistant_offline_probe import main as offline_probe_main

        raise SystemExit(offline_probe_main(sys.argv[2:]))
    elif getattr(sys, "frozen", False) and sys.argv[1:2] == ["--_codeviz-assistant-native-smoke"]:
        # Fixed, bundled fake only. No provider/module/project/endpoint inputs.
        from assistant_smoke_host import main as smoke_main

        smoke_main(sys.argv[2:])
    else:
        from codeviz.ui.desktop import main

        main()
else:
    from setuptools.command.build_py import build_py

    class BuildFrontendAssets(build_py):
        """Copy the Vite output into the wheel without changing the source tree."""

        def run(self) -> None:
            if self.editable_mode:
                super().run()
                return
            root = Path(__file__).resolve().parents[1]
            frontend = root / "frontend" / "dist"
            if not (frontend / "index.html").is_file():
                raise RuntimeError("Run npm --prefix frontend ci and npm --prefix "
                                   "frontend run build before building a wheel")
            super().run()
            destination = Path(self.build_lib) / "codeviz" / "ui" / "assets"
            # Remove stale hashed assets only from this command's build directory.
            import shutil

            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(frontend, destination)

        def get_source_files(self) -> list[str]:
            sources = super().get_source_files()
            root = Path(__file__).resolve().parents[1]
            sources.append("scripts/desktop_entry.py")
            sources.extend(str(path.relative_to(root)) for path in
                           (root / "frontend" / "dist").rglob("*") if path.is_file())
            return sources
