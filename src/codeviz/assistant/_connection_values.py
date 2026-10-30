"""Closed internal values for connection capability evidence."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConnectionCapabilities:
    tool_supported: bool
    structured_output_supported: bool
    json_object_supported: bool
    output_validation: str

    def __post_init__(self) -> None:
        if (type(self.tool_supported) is not bool or self.tool_supported is not True
                or type(self.structured_output_supported) is not bool
                or self.structured_output_supported is not False
                or type(self.json_object_supported) is not bool
                or self.json_object_supported is not True
                or type(self.output_validation) is not str
                or self.output_validation != "local-json-schema"):
            raise ValueError("Invalid connection capabilities.")


def _copy_connection_capabilities(value: object) -> ConnectionCapabilities:
    """Revalidate and copy an exact capability record at a trust boundary."""
    if type(value) is not ConnectionCapabilities:
        raise ValueError("Invalid connection capabilities.")
    value.__post_init__()
    return ConnectionCapabilities(
        value.tool_supported,
        value.structured_output_supported,
        value.json_object_supported,
        value.output_validation,
    )
