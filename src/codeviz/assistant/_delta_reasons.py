"""Closed private facts, never provider values or publication authority."""

from dataclasses import dataclass

REASONS = (
    "delta_object", "delta_fields", "delta_refusal", "delta_role", "delta_reasoning",
    "content_type", "content_string_size", "content_encoding", "content_total_size",
    "tool_mode", "tool_list", "tool_count", "tool_item_object", "tool_item_fields",
    "tool_index", "tool_type", "tool_function_object", "tool_function_fields",
    "fragment_type", "fragment_string_size", "fragment_encoding", "fragment_joined_size",
    "terminal_duplicate_id", "terminal_missing_id", "terminal_unknown_name",
    "arguments_json", "arguments_object", "tool_payload_size",
)


@dataclass(frozen=True, slots=True)
class DeltaRejection:
    reason: str
    boundary: str = "dispatch"
    check_site: str = "delta_shape"

    def __post_init__(self) -> None:
        if (type(self.reason) is not str or self.reason not in REASONS
                or type(self.boundary) is not str or self.boundary != "dispatch"
                or type(self.check_site) is not str or self.check_site != "delta_shape"):
            raise ValueError("Invalid private rejection.")


def validated_rejection(value: object) -> DeltaRejection | None:
    if type(value) is not DeltaRejection:
        return None
    try:
        value.__post_init__()
    except (ValueError, AttributeError, TypeError):
        return None
    return value
