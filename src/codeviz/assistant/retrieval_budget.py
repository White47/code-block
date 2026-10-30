"""Private conservative planning before provider preparation and admission."""

from __future__ import annotations

import json
from typing import Literal

from .budget import BudgetLimits, BudgetUsage, ExplanationBudgetConfig
from .interpretation_origin import (
    PriorInterpretationOrigin,
    interpretation_footprint,
    interpretation_tags,
)
from .provider import ProviderDraft
from .source_policy import SourceExcerptError, validate_source_path

_INPUT_LIMIT = 20_000
_OUTPUT_LIMIT = 2_000
_FINAL_RESERVE = 512


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate internal draft key.")
        value[key] = item
    return value


def _source_footprint(draft: ProviderDraft, *,
        trusted_interpretations: tuple[PriorInterpretationOrigin, ...] = ()) -> tuple[int, frozenset[str]]:
    if type(draft) is not ProviderDraft or type(draft.body) is not bytes:
        raise ValueError("Invalid source headroom draft.")
    value = json.loads(
        draft.body.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )
    if (type(value) is not dict or type(value.get("sourceTags")) is not list
            or type(value.get("metadataTags", [])) is not list
            or type(value.get("messages")) is not list):
        raise ValueError("Invalid source headroom draft.")
    strings: dict[str, str] = {}

    def walk(item: object, pointer: str) -> None:
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("Invalid source headroom key.")
                escaped = key.replace("~", "~0").replace("/", "~1")
                strings[pointer + "/" + escaped + "#key"] = key
                walk(child, pointer + "/" + escaped)
        elif type(item) is list:
            for index, child in enumerate(item):
                walk(child, pointer + f"/{index}")
        elif type(item) is str:
            strings[pointer] = item

    walk(value["messages"], "/messages")
    source_characters = 0
    files: set[str] = set()
    approved: set[tuple[str, int, int]] = set()
    for key in ("sourceTags", "metadataTags"):
        tags = value[key] if key in value else []
        for tag in tags:
            if type(tag) is not dict or set(tag) != {"pointer", "start", "end", "file"}:
                raise ValueError("Invalid source headroom tag.")
            pointer, start, end, file = (
                tag["pointer"], tag["start"], tag["end"], tag["file"])
            if (type(pointer) is not str or not pointer.startswith("/messages/")
                    or len(pointer) > 2_048
                    or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= 2**53 - 1 or type(file) is not str):
                raise ValueError("Invalid source headroom tag.")
            pointer.encode("utf-8", "strict")
            content = strings.get(pointer)
            location = (pointer, start, end)
            if content is None or end > len(content) or location in approved:
                raise ValueError("Unresolved source headroom tag.")
            approved.add(location)
            files.add(validate_source_path(file))
            if key == "sourceTags":
                source_characters += end - start
    derived_count, derived_files, _locations = interpretation_footprint(
        interpretation_tags(value), strings, trusted_interpretations, approved)
    source_characters += derived_count
    files.update(derived_files)
    if len({path.casefold() for path in files}) != len(files):
        raise ValueError("Conflicting source headroom paths.")
    return source_characters, frozenset(files)


class SourceHeadroomPlanner:
    """Exact logical source/file routing without reservation or mutation."""

    def __init__(self, limits: BudgetLimits, *,
                 trusted_interpretations: tuple[PriorInterpretationOrigin, ...] = ()) -> None:
        if type(limits) is not BudgetLimits:
            raise ValueError("Invalid source headroom limits.")
        limits.__post_init__()
        self._max_source_characters = limits.max_source_characters
        self._max_files = limits.max_files
        self._trusted_interpretations = trusted_interpretations

    def fits(self, usage: BudgetUsage, *drafts: ProviderDraft) -> bool:
        if type(usage) is not BudgetUsage or not drafts:
            raise ValueError("Invalid source headroom usage.")
        source_characters = usage.source_characters
        files = set(usage.files)
        try:
            for draft in drafts:
                count, draft_files = _source_footprint(draft, trusted_interpretations=self._trusted_interpretations)
                source_characters += count
                files.update(draft_files)
                if (source_characters > self._max_source_characters
                        or len(files) > self._max_files
                        or len({path.casefold() for path in files}) != len(files)):
                    return False
        except (SourceExcerptError, TypeError, ValueError, UnicodeError):
            return False
        return True


def output_allocation(
    usage: BudgetUsage,
    phase: Literal["ordinary", "final", "repair"],
    requested: int,
    *,
    config: ExplanationBudgetConfig | None = None,
) -> int | None:
    """Allocate one phase while preserving configured final and repair capacity."""
    if (type(usage) is not BudgetUsage or type(phase) is not str
            or phase not in ("ordinary", "final", "repair")
            or type(requested) is not int or requested < 1):
        raise ValueError("Invalid retrieval output budget.")
    if config is not None and type(config) is not ExplanationBudgetConfig:
        raise ValueError("Invalid retrieval output budget.")
    output_limit = _OUTPUT_LIMIT if config is None else config.max_output_tokens
    ordinary_limit = 512 if config is None else config.ordinary_output_tokens
    final_limit = requested if config is None else config.final_output_tokens
    repair_limit = requested if config is None else config.repair_output_tokens
    remaining = output_limit - usage.output_tokens
    if phase == "ordinary":
        remaining -= (_FINAL_RESERVE if config is None else
                      config.final_output_tokens + config.repair_output_tokens)
        if remaining <= 0:
            return None
        allocation = ordinary_limit if config is not None else min(requested, ordinary_limit)
        if config is not None and remaining < allocation:
            return None
        return min(allocation, remaining)
    if remaining <= 0:
        return None
    phase_limit = final_limit if phase == "final" else repair_limit
    allocation = phase_limit if config is not None else min(requested, phase_limit)
    if config is not None and remaining < allocation:
        return None
    return min(allocation, remaining)


class InputBudgetPlanner:
    """Use logical UTF-8 plus observed framing only as a pre-prepare routing estimate."""

    def __init__(self, input_limit: int = _INPUT_LIMIT) -> None:
        if type(input_limit) is not int or input_limit < 1:
            raise ValueError("Invalid retrieval input budget.")
        self._input_limit = input_limit
        self._wire_overhead = 0

    def observe(self, draft: ProviderDraft, reserved_input: int) -> None:
        if (type(draft) is not ProviderDraft or type(reserved_input) is not int
                or reserved_input < 0):
            raise ValueError("Invalid retrieval input observation.")
        self._wire_overhead = max(
            self._wire_overhead,
            max(0, reserved_input - len(draft.body)),
        )

    def estimated_input(self, draft: ProviderDraft) -> int:
        if type(draft) is not ProviderDraft:
            raise ValueError("Invalid retrieval input draft.")
        return len(draft.body) + self._wire_overhead

    def fits(self, usage: BudgetUsage, *drafts: ProviderDraft) -> bool:
        if type(usage) is not BudgetUsage or not drafts:
            raise ValueError("Invalid retrieval input budget.")
        return (usage.input_tokens + sum(self.estimated_input(draft) for draft in drafts)
                <= self._input_limit)

