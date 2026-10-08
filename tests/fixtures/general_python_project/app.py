"""Harmless ordinary CLI fixture for static native UI acceptance."""

from helpers import NativeReportBuilder, native_format_summary, native_normalize_record


def native_cli_entry(record="native preview"):
    normalized_cli_record = native_normalize_record(record)
    report_builder = NativeReportBuilder()
    cli_report = report_builder.native_build_report(normalized_cli_record)
    return native_format_summary(cli_report)


if __name__ == "__main__":
    native_cli_entry()
