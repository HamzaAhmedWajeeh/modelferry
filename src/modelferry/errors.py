"""Pack-side error types carrying SPEC section 10 exit codes.

offline.py has its own independent hierarchy (it imports nothing from this
package); these are for the connected-side pack path and the CLI wrappers.
"""

from __future__ import annotations


class PackError(Exception):
    """Base for anticipated pack-side failures. Prints one line, no traceback."""

    exit_code = 1


class UsageError(PackError):
    exit_code = 2  # bad arguments, pre-flight payload violations


class SourceError(PackError):
    exit_code = 3  # network failure, HF auth / 404 / gated-without-token


class LocalFsError(PackError):
    exit_code = 4  # permissions, disk full, destination already exists
