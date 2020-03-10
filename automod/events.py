from redbot.core.commands import Context
from redbot.core.i18n import Translator

from .errors import InvalidEventError

_ = Translator('AutoMod', __file__)

GLOBAL_EVENTS = [
    'mention_spam',
    'message_spam',
    'attachment_spam',
    'filter_invites',
    'filter_messages'
]

SPAM_EVENTS = ['mention_spam', 'message_spam', 'attachment_spam']

DURATION_EVENTS = ['message_spam', 'attachment_spam']


class Event:
    """A class to represent an automod event."""

    def __init__(self, name: str):
        self.name = name

    @classmethod
    async def convert(cls, ctx: Context, argument: str):
        if ctx.command.name in ['limit', 'mute']:
            events = SPAM_EVENTS
        elif ctx.command.name == 'duration':
            events = DURATION_EVENTS
        else:
            events = GLOBAL_EVENTS

        if argument in events:
            return cls(argument)
        else:
            raise InvalidEventError(
                _("{} is not a valid event for this command.").format(argument)
            )
