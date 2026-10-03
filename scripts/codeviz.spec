"""Build a Windows onedir application; do not start a native window."""
# ruff: noqa: F821 -- PyInstaller injects SPECPATH and build target classes.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve().parent
frontend = root / "frontend" / "dist"
if not (frontend / "index.html").is_file():
    raise RuntimeError("Build frontend/dist before running PyInstaller")

webview_data, webview_binaries, webview_imports = collect_all("webview")
a = Analysis(
    [str(root / "scripts" / "desktop_entry.py")],
    pathex=[str(root / "src")],
    binaries=webview_binaries,
    datas=[(str(frontend), "frontend/dist"), *webview_data],
    hiddenimports=["codeviz.ui.desktop", *webview_imports],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="CodeViz",
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, name="CodeViz",
)
