"""Own-written library, OO and async snippets; analyzed without execution."""


def native_normalize_record(record):
    normalized_record = record.strip()
    return normalized_record.upper()


def native_format_summary(value):
    formatted_summary = "Preview: " + value
    return formatted_summary


class NativeReportBuilder:
    def native_build_report(self, value):
        report_text = "Report " + value
        return report_text


async def native_async_preview(record):
    preview_record = native_normalize_record(record)
    return preview_record
