from discord.ext.commands import BadArgument, CheckFailure


class InvalidEventError(BadArgument):
    """Raised when an event is not found."""


class LogNotSet(CheckFailure):
    """Raised if log channel not set."""
