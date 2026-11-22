"""Exceptions with distinct exit codes so CI can tell a broken config from a broken endpoint."""


class SentinelError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(SentinelError):
    """sentinel.yaml is missing, unreadable, or invalid."""


class KevError(SentinelError):
    """The local Kev server could not be reached or returned an invalid response."""


class BaselineError(SentinelError):
    """An endpoint has no baseline yet (run `sentinel init` or `sentinel baseline set`)."""