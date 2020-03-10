from redbot.core import commands
from redbot.core.i18n import Translator

from .errors import LogNotSet

_ = Translator("AutoMod", __file__)


def is_log_set():
    """A decorator to check if log channel is set."""

    async def predicate(ctx: commands.Context):
        cog = ctx.cog
        if cog:
            config = cog.config

            log_channel = await config.guild(ctx.guild).log_channel()
            if not log_channel:
                raise LogNotSet(_(
                    "AutoMod log channel is not set. Please set it"
                    " before using other AutoMod comamnds."
                ))

        return True

    return commands.check(predicate)
