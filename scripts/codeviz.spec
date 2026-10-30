"""Build a Windows onedir application; do not start a native window."""
# ruff: noqa: F821 -- PyInstaller injects SPECPATH and build target classes.

import ssl
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).resolve().parent
frontend = root / "frontend" / "dist"
if not (frontend / "index.html").is_file():
    raise RuntimeError("Build frontend/dist before running PyInstaller")

# Some build runtimes load a mandatory CA policy beside ssl.py during every
# SSLContext construction. Preserve that exact runtime file and location;
# standard Python has no supplemental policy. Never bypass TLS verification.
ssl_policy = Path(ssl.__file__).with_name("codex-rejected-cas.pem")
ssl_data = [(str(ssl_policy), ".")] if ssl_policy.is_file() else []

webview_data, webview_binaries, webview_imports = collect_all("webview")
# Only the production adapter's dynamic entrypoints, not every AI provider.
sdk_imports = [
    "httpx2", "openai", "pydantic_ai.models", "pydantic_ai.messages",
    "pydantic_ai.models.openai", "pydantic_ai.providers.openai",
    "pydantic_ai.profiles.openai", "pydantic_ai.output", "pydantic_ai.tools",
]
sdk_distributions = [
    "pydantic-ai-slim", "openai", "httpx2", "httpcore2", "pydantic",
    "pydantic-core", "pydantic-graph", "genai-prices", "tiktoken",
]
webview_imports.extend(sdk_imports)
for distribution in sdk_distributions:
    webview_data.extend(copy_metadata(distribution))
a = Analysis(
    [str(root / "scripts" / "desktop_entry.py")],
    pathex=[str(root / "src")],
    binaries=webview_binaries,
    datas=[(str(frontend), "frontend/dist"), *webview_data, *ssl_data],
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
