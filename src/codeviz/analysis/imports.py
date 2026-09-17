"""Project-local module names and explicit relative import paths.

This catalog uses indexed files only. It never calls Python's import machinery.
"""

from collections import defaultdict

from codeviz.core import NodeKind

from .python import ParsedFile
from .symbols import SymbolIndex, SymbolIndexEntry


class ModuleCatalog:
    def __init__(self, files: tuple[ParsedFile, ...], index: SymbolIndex) -> None:
        modules: dict[str, list[SymbolIndexEntry]] = defaultdict(list)
        self.by_file: dict[str, SymbolIndexEntry] = {}
        self.known_names: set[str] = set()
        for parsed in files:
            names = self._names(parsed.module_name, parsed.relative_path)
            self.known_names.update(names)
        for entry in index.entries.values():
            if entry.kind != NodeKind.MODULE:
                continue
            self.by_file[entry.file_path] = entry
            for name in self._names(entry.qualified_name, entry.file_path):
                modules[name].append(entry)
        self.modules = {name: tuple(entries) for name, entries in modules.items()}

    @staticmethod
    def _names(module_name: str, file_path: str) -> tuple[str, ...]:
        if file_path.startswith("src/") and module_name.startswith("src."):
            return module_name, module_name[4:]
        return (module_name,)

    def candidates(self, module_name: str) -> tuple[SymbolIndexEntry, ...]:
        return self.modules.get(module_name, ())

    def is_project_path(self, dotted_path: str) -> bool:
        return any(name == dotted_path or name.startswith(f"{dotted_path}.")
                   or dotted_path.startswith(f"{name}.") for name in self.known_names)

    def absolute_import(self, entry: SymbolIndexEntry) -> str | None:
        raw = entry.metadata.get("imported_from")
        if raw is None:
            return None
        if not raw.startswith("."):
            return raw.split(".")[0] if entry.metadata.get("binds_root") == "true" else raw
        module = self.by_file.get(entry.file_path)
        if module is None:
            return None
        package_name = module.qualified_name if entry.file_path.endswith("/__init__.py") else module.qualified_name.rpartition(".")[0]
        package_parts = package_name.split(".") if package_name else []
        level = len(raw) - len(raw.lstrip("."))
        if level - 1 >= len(package_parts):
            return None
        relative_parts = raw[level:].split(".") if raw[level:] else []
        return ".".join((*package_parts[:len(package_parts) - (level - 1)], *relative_parts))
