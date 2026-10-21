"""Errors people can act on: one line saying what happened, one saying what to try.

Every failure sigstar expects is raised as SigstarError with a hint. Anything else is
treated as a bug by the CLI, which prints a report hint (and the traceback with --debug).
"""

ISSUES_URL = "https://github.com/Bit-Primitive/sigstar/issues"


class SigstarError(Exception):
    """A failure with a plain explanation and a next step."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


def bug_report_hint() -> str:
    return f"this looks like a bug: run again with --debug and report it at {ISSUES_URL}"


# (words in the lowercased error text, hint) for errors raised by libraries, not by us.
_LIBRARY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("could not set lock", "conflicting lock"),
        "another program has the DuckDB file open (the duckdb tool, a notebook, a SQL "
        "editor); close it and try again",
    ),
    (
        ("permission denied", "read-only file system"),
        "choose a folder you can write to (warehouse: in sigstar.yml)",
    ),
    (("no space left",), "free up disk space, or point warehouse: in sigstar.yml at another disk"),
)


def hint_for(error: BaseException) -> str | None:
    if isinstance(error, SigstarError):
        return error.hint
    text = str(error).lower()
    for words, hint in _LIBRARY_HINTS:
        if any(word in text for word in words):
            return hint
    return None
