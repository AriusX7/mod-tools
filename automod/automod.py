import asyncio
import logging
import re
from collections import defaultdict
from typing import Union

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.i18n import Translator, cog_i18n
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
from redbot.core.utils.mod import is_mod_or_superior
# from redbot.core.modlog import Case, create_case, get_modlog_channel,

from .errors import LogNotSet
from .events import Event
from .utils import is_log_set

log = logging.getLogger("red.automod")

default_guild = {
    'log_channel': None,
    'automod_duration': 5,
    'mention_spam': {
        'enabled': False,
        'limit': 0,
        'mute': False,
        'colour': None
    },
    'message_spam': {
        'enabled': False,
        'limit': 0,
        'mute': False,
        'colour': None
    },
    'attachment_spam': {
        'enabled': False,
        'limit': 0,
        'mute': False,
        'colour': None
    },
    'filter_invites': {
        'enabled': False,
        'whitelist': [],
        'colour': None
    },
    'filter_messages': {
        'enabled': False,
        'filter': [],
        'colour': None
    },
    'ignored': {
        'roles': [],
        'channels': [],
        'members': []
    }
}

default_member = {}

_ = Translator("AutoMod", __file__)

check_mark = "\N{WHITE HEAVY CHECK MARK}"
invite_regex = re.compile(
    r"((http(s|):\/\/|)(discord)(\.(gg|io|me)"
    r"\/|app\.com\/invite\/)([0-z]+))"
)

__version__ = "1.0.0"


@cog_i18n(_)
class AutoMod(commands.Cog):
    """AutoMod commands and functions."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.messages = defaultdict(list)
        self.attachments = defaultdict(list)

        self.config = Config.get_conf(
            self, 1_330_157_707, force_registration=True
        )

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        self.mute = self.bot.get_command("mute")

    @commands.group(name='automod')
    @commands.guild_only()
    @commands.admin_or_permissions()
    async def _automod(self, ctx: Context):
        """Set automod settings!"""

        if not ctx.invoked_subcommand:
            await ctx.invoke(
                self.bot.get_command('automod settings')
            )

    @_automod.command(name='toggle')
    @is_log_set()
    async def _automod_toggle(self, ctx: Context, *events: Event):
        """Toggle specified, space separated, automod events.

        The following are valid events:
        `mention_spam`
        `message_spam`
        `attachment_spam`
        `filter_invites`
        `filter_messages`
        """

        guild = ctx.guild

        if not events:
            return await ctx.send("Invalid events!")

        changes = ""

        conf = await self.config.guild(guild).all()

        for event in events:
            old = conf[event.name]['enabled']
            new = True if not old else False
            await self.config.guild(guild).set_raw(
                event.name, 'enabled', value=new
            )
            changes += f"\n`{event.name}`: `{new}`"

        if changes:
            changes = _("Updated the following:\n{}").format(changes)
        else:
            changes = "No changes."

        await ctx.send(changes)

    @_automod.command(name='limit')
    @is_log_set()
    async def _automod_limit(
        self, ctx: Context, limit: int, *events: Event
    ):
        """Set limit for specified, comma-separated automod events.

        The following are valid events:
        `mention_spam`
        `message_spam`
        `attachment_spam`
        """

        guild = ctx.guild

        if not events:
            return await ctx.send("Invalid events!")

        changes = ""

        for event in events:
            await self.config.guild(guild).set_raw(
                event.name, 'limit', value=limit
            )
            changes += f"\n`{event.name}`: `{limit}`"

        if changes:
            changes = _(
                "Updated limit of the following:\n{}"
            ).format(changes)
        else:
            changes = "No changes."

        await ctx.send(changes)

    @_automod.command(name='duration')
    @is_log_set()
    async def _automod_duration(self, ctx: Context, duration: int):
        """Set duration, in seconds, for automod events."""

        await self.config.guild(ctx.guild).automod_duration.set(duration)

        await ctx.send(
            _("Set automod duration to {} seconds.").format(duration)
            )

    @_automod.command(name='channel')
    async def _automod_log_channel(
        self, ctx: Context, channel: discord.TextChannel
    ):
        """Set channel for logging automod events."""

        await self.config.guild(ctx.guild).log_channel.set(channel.id)

        await ctx.send(
            _("Set {} as automod log channel.").format(channel.mention)
        )

    @_automod.command(name='mute')
    @is_log_set()
    async def _automod_mute(
        self, ctx: Context, setting: bool, *events: Event
    ):
        """Enable/disable mute for specified, comma-separated automod events.

        The following are valid events:
        `mention_spam`
        `message_spam`
        `attachment_spam`
        """

        guild = ctx.guild

        if not events:
            return await ctx.send("Invalid events!")

        changes = ""

        for event in events:
            await self.config.guild(guild).set_raw(
                event.name, 'mute', value=setting
            )
            changes += f"\n`{event.name}`: `{setting}`"

        if changes:
            changes = _(
                "Updated mute of the following:\n{}"
            ).format(changes)
        else:
            changes = "No changes."

        await ctx.send(changes)

    @_automod.command(name='ignore')
    @is_log_set()
    async def _automod_ignore(
        self,
        ctx: Context,
        obj: Union[discord.Role, discord.Member, discord.TextChannel]
    ):
        """Add role, member or channel to ignore list.

        Groups in ignore list are ignored from automod checks.
        """

        async with self.config.guild(ctx.guild).ignored() as ignored:
            if isinstance(obj, discord.Role):
                ignored['roles'].append(obj.id)
            elif isinstance(obj, discord.Member):
                ignored['members'].append(obj.id)
            elif isinstance(obj, discord.TextChannel):
                ignored['channels'].append(obj.id)

        await ctx.message.add_reaction(check_mark)

    @_automod.command(name='unignore')
    @is_log_set()
    async def _automod_unignore(
        self,
        ctx: Context,
        obj: Union[discord.Role, discord.Member, discord.TextChannel]
    ):
        """Remove role, member or channel from ignore list.

        Groups in ignore list are ignored from automod checks.
        """

        async with self.config.guild(ctx.guild).ignored() as ignored:
            if isinstance(obj, discord.Role):
                try:
                    ignored['roles'].remove(obj.id)
                except ValueError:
                    pass
            elif isinstance(obj, discord.Member):
                try:
                    ignored['members'].remove(obj.id)
                except ValueError:
                    pass
            elif isinstance(obj, discord.TextChannel):
                try:
                    ignored['channels'].remove(obj.id)
                except ValueError:
                    pass

        await ctx.message.add_reaction(check_mark)

    @_automod.command(name='colour', aliases=['color'])
    @is_log_set()
    async def _automod_colour(self, ctx: Context, colour: discord.Colour, *events: Event):
        """Set color for specified, space separated, automod events.

        `colour` must be a hex code or a 
        [built colour](https://discordpy.readthedocs.io/en/latest/api.html#colour).

        The following are valid events:
        `mention_spam`
        `message_spam`
        `attachment_spam`
        `filter_invites`
        `filter_messages`
        """

        guild = ctx.guild

        if not events:
            return await ctx.send("Invalid events!")

        if colour:
            colour = colour.value

        changes = ""

        for event in events:
            await self.config.guild(guild).set_raw(
                event.name, 'colour', value=colour
            )
            changes += f"\n`{event.name}`: `{colour}`"

        if changes:
            changes = _("Updated the following:\n{}").format(changes)
        else:
            changes = "No changes."

        await ctx.send(changes)

    @_automod.group(name='filter')
    @is_log_set()
    async def _filter(self, ctx: Context):
        """Automod filter settings."""
        pass

    @_filter.command(name='add')
    async def _filter_add(self, ctx: Context, *, regex: str):
        """Add regex to the automod filter."""

        async with self.config.guild(ctx.guild).filter_messages() as f:
            f['filter'].append(regex)

        await ctx.message.add_reaction(check_mark)

    @_filter.command(name='view')
    async def _filter_view(self, ctx: Context):
        """View the automod filter."""

        filter_ = await self.config.guild(ctx.guild).get_raw(
            'filter_messages', 'filter'
        )

        embeds = []

        if len(filter_) < 1:
            return await ctx.send(_("Empty filter!"))

        for num, regex in enumerate(filter_, start=1):
            info = _("Number: **{}**\nRegex:\n```bf\n{}```").format(
                num,
                regex
            )
            embed = discord.Embed(
                color=await ctx.embed_colour(),
                title=_("Filter for {}").format(ctx.guild.name),
                description=info,
                timestamp=ctx.message.created_at
            )

            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)

            embed.set_footer(
                text=_("Page {page}/{leng}").format(
                    page=num, leng=len(filter_)
                )
            )

            embeds.append(embed)

        await menu(ctx, embeds, DEFAULT_CONTROLS)

    @_filter.command(name='remove')
    async def _filter_remove(self, ctx: Context, number: int):
        """Remove pattern from the automod filter."""

        async with self.config.guild(ctx.guild).filter_messages() as f:
            f['filter'].pop(number-1)

        await ctx.message.add_reaction(check_mark)

    @_automod.group(name='invite')
    async def _invite(self, ctx: Context):
        """Automod invite settings."""
        pass

    @_invite.command(name='add')
    async def _invite_add(self, ctx: Context, server_id: int):
        """Add server id to the invite whitelist."""

        async with self.config.guild(ctx.guild).filter_invites() as f:
            f['whitelist'].append(server_id)

        await ctx.message.add_reaction(check_mark)

    @_invite.command(name='view')
    async def _invite_view(self, ctx: Context):
        """View servers in the invite whitelist."""

        async with self.config.guild(ctx.guild).filter_invites() as f:
            whitelist = f['whitelist']

        guilds_info = ""
        for guild_id in whitelist:
            guild = self.bot.get_guild(guild_id)
            if guild:
                guilds_info += f"\n{guild_id} \"{guild.name}\""
            else:
                guilds_info += _("\n{} \"Unable to fetch server details\"").format(guild_id)

        if guilds_info.strip():
            guilds_info = f"```apache\n{guilds_info.strip()}```"
        else:
            guilds_info = _("No servers in the whitelist!")

        embed = discord.Embed(
            color=await ctx.embed_colour(),
            title=_("Invite whitelist for {}").format(ctx.guild.name),
            description=guilds_info
        )

        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url)

        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(guilds_info)

    @_invite.command(name='remove')
    async def _invite_remove(self, ctx: Context, server_id: int):
        """Remove server id from the invite whitelist."""

        async with self.config.guild(ctx.guild).filter_invites() as f:
            try:
                f['whitelist'].remove(server_id)
            except ValueError:
                pass

        await ctx.message.add_reaction(check_mark)

    @_automod.command(name='settings')
    async def _automod_settings(self, ctx: Context):
        """Display automod settings."""

        guild: discord.Guild = ctx.guild

        settings = await self.config.guild(guild).all()
        general_sets = await self.get_settings(guild, settings)

        embed=discord.Embed(
            color=await ctx.embed_colour(),
            description=general_sets,
            title="AutoMod Settings"
        )
        embed = self.ignore_fields(guild, settings, embed)
        try:
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(_("Please give me embed link permissions."))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild.id:
            return

        if message.author == message.guild.me:
            return

        if await is_mod_or_superior(bot=self.bot, obj=message):
            return

        settings = await self.config.guild(message.guild).all()

        if self.is_ignored_group(message, settings):
            return

        # mention spam
        if (
            settings['mention_spam']['enabled']
            and self.is_event_set(settings['mention_spam'])
            and self.total_mentions(message) 
                > settings['mention_spam']['limit']
        ):
            await message.delete()

            if settings['mention_spam']['mute']:
                await self.spam_mute(message)
            
            await self.mention_spam_log(message, settings)

        # attachment spam
        elif (
            message.attachments
            and settings['attachment_spam']['enabled']
            and self.attachment_spam_condition(
                message, settings['attachment_spam']
            )
        ):
            await message.delete()
            msg_ids = self.attachments.get(message.author.id)
            for m_id in msg_ids:
                try:
                    msg = await message.channel.fetch_message(m_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
            
            if settings['attachment_spam']['mute']:
                await self.spam_mute(message)

            await self.attachment_spam_log(message, settings, 1+len(msg_ids))

        # message spam
        elif (
            settings['message_spam']['enabled']
            and self.message_spam_condition(
                message, settings['message_spam']
            )
        ):
            await message.delete()
            msg_ids = self.messages.get(message.author.id)
            for m_id in msg_ids:
                try:
                    msg = await message.channel.fetch_message(m_id)
                    await msg.delete()
                except discord.NotFound:
                    pass

            if settings['message_spam']['mute']:
                await self.spam_mute(message)

            await self.message_spam_log(message, settings, 1+len(msg_ids))

        # invites
        elif settings['filter_invites']['enabled']:
            whitelist = settings['filter_invites']['whitelist']

            invite_match = re.findall(invite_regex, message.content)
            if invite_match:
                try:
                    invite = await self.bot.fetch_invite(invite_match[-1][-1])
                    if not (
                        invite.guild.id == message.guild.id
                        or invite.guild.id in whitelist
                    ):
                        await message.delete()
                        await self.invite_log(message, settings, invite)
                except discord.NotFound:
                    await message.delete()
                    await self.invite_log(message, settings, invite_match[-1][-1])

        # message filter
        if (
            settings['filter_messages']['enabled']
        ):
            filter_ = settings['filter_messages']['filter']
            for pattern in filter_:
                match = re.search(pattern, message.content)
                if match:
                    await message.delete()
                    await self.filter_log(message, settings, match)

        if message.attachments:
            self.attachments[message.author.id].append(message.id)
            self.messages[message.author.id].append(message.id)
            await asyncio.sleep(settings['automod_duration'])
            self.attachments[message.author.id].remove(message.id)
            self.messages[message.author.id].remove(message.id)
            return 

        self.messages[message.author.id].append(message.id)
        await asyncio.sleep(settings['automod_duration'])
        self.messages[message.author.id].remove(message.id)

    async def get_settings(self, guild: discord.Guild, settings: dict):
        """Return string containing general automod settings."""

        log_id = settings['log_channel']
        if log_id:
            log_channel = discord.utils.get(guild.channels, id=log_id).mention
        else:
            log_channel = "Not set"

        info = f"Log Channel: {log_channel}"

        mention_spam = settings['mention_spam']
        message_spam = settings['message_spam']
        attachment_spam = settings['attachment_spam']
        filter_invites = settings['filter_invites']
        filter_messages = settings['filter_messages']

        if mention_spam['enabled']:
            info += "\n\n`mention_spam`: `Enabled`"

            if self.is_event_set(mention_spam):
                info += (
                    f" (Limit: {mention_spam['limit']}"
                    " mentions per message)"
                )
            else:
                info += " (Not set)"
        else:
            info += "\n\n`mention_spam`: `Not Enabled`"

        if message_spam['enabled']:
            info += "\n`message_spam`: `Enabled`"

            if self.is_event_set(message_spam):
                info += (
                    f" (Limit: {message_spam['limit']} messages per"
                    f" {settings['automod_duration']} seconds)"
                )
            else:
                info += " (Not set)"
        else:
            info += "\n`message_spam`: `Not Enabled`"

        if attachment_spam['enabled']:
            info += "\n`attachment_spam`: `Enabled`"

            if self.is_event_set(attachment_spam):
                info += (
                    f" (Limit: {attachment_spam['limit']} attachments per"
                    f" {settings['automod_duration']} seconds)"
                )
            else:
                info += " (Not set)"
        else:
            info += f"\n`attachment_spam`: `Not Enabled`"

        info += f"\n`filter_invites`: `{filter_invites['enabled']}`"
        info += f"\n`filter_messages`: `{filter_messages['enabled']}`"

        return info

    def ignore_fields(
        self, guild: discord.Guild, settings: dict, embed: discord.Embed
    ):
        """Add ignored fields to embed."""

        # settings = await self.config.guild(guild)
        ignored = settings['ignored']

        role_info = ""
        for role_id in ignored['roles']:
            role_info += f"\n{guild.get_role(role_id).mention}"

        member_info = ""
        for member_id in ignored['members']:
            member_info += f"\n{guild.get_member(member_id).mention}"

        channel_info = ""
        for channel_id in ignored['channels']:
            channel_id += f"\n{guild.get_channel(channel_id).mention}"

        embed.add_field(
            name="Ignored Roles", value=role_info.strip() or "No roles"
        )
        embed.add_field(
            name="Ignored Members", value=member_info.strip() or "No members"
        )
        embed.add_field(
            name="Ignored Channels", value=channel_info.strip() or "No channels"
        )

        return embed

    def is_event_set(self, event_settings: dict):
        """Return True if event settings are changed."""

        is_set = True

        try:
            if not event_settings['limit']:
                is_set = False
        except KeyError:
            pass

        return is_set

    def message_spam_condition(self, m: discord.Message, spam_settings):
        if not self.is_event_set(spam_settings):
            return
        if (
            len(self.messages.get(m.author.id, []))
            > spam_settings['limit']
        ):
            return True

    def attachment_spam_condition(self, m: discord.Message, spam_settings):
        if not self.is_event_set(spam_settings):
            return
        if (
            len(self.attachments.get(m.author.id, []))
            >= spam_settings['limit']
        ):
            return True

    async def cog_command_error(self, ctx: Context, error: Exception):
        if not isinstance(
            getattr(error, "original", error),
            (
                # commands.CheckFailure,
                commands.UserInputError,
                commands.DisabledCommand,
                commands.CommandOnCooldown,
            ),
        ):
            if isinstance(error, LogNotSet):
                await ctx.send(error)

        await ctx.bot.on_command_error(
            ctx, getattr(error, "original", error), unhandled_by_cog=True
        )

    async def spam_mute(self, message: discord.Message):
        """Mute user for spam."""

        ctx = await self.bot.get_context(message)

        await ctx.invoke(
            self.mute, message.author, duration="5h",
            reason="Automatic spam detection", silent=True
        )

    def is_ignored_group(self, message: discord.Message, settings: dict):
        """Check if the message is from or in an ignored group."""

        ignored = settings['ignored']

        if message.author.id in ignored['members']:
            return True

        if message.channel.id in ignored['channels']:
            return True

        for role in message.author.roles:
            if role.id in ignored['roles']:
                return True

    async def mention_spam_log(self, message: discord.Message, settings: dict):
        """Send embed with mention spam log message."""

        embed = discord.Embed(
            color=self.get_event_colour('mention_spam', settings),
            description=message.content,
            timestamp=message.created_at
        )

        embed.add_field(
            name="Channel",
            value=message.channel.mention
        )

        embed.add_field(
            name="Reason",
            value=_("Mention Spam ({} mentions per message)").format(
                settings['mention_spam']['limit']
            )
        )

        embed.set_author(
            name=_("{} - Message Deleted").format(message.author),
            icon_url=message.author.avatar_url
        )
        embed.set_footer(text=_("User ID: {}").format(message.author.id))

        await self.send_log_msg(message.guild, embed, settings)

    async def attachment_spam_log(
        self, message: discord.Message, settings: dict, total: int
    ):
        """Send embed with attachment spam log message."""

        embed = discord.Embed(
            color=self.get_event_colour('attachment_spam', settings),
            description=", ".join(a.filename for a in message.attachments),
            timestamp=message.created_at
        )

        embed.add_field(
            name="Channel",
            value=message.channel.mention
        )

        embed.add_field(
            name="Reason",
            value=_("Attachment Spam ({} attachments/{} secs)").format(
                settings['attachment_spam']['limit'],
                settings['automod_duration']
            )
        )

        embed.set_author(
            name=_("{} - {} Messages Deleted").format(message.author, total),
            icon_url=message.author.avatar_url
        )
        embed.set_footer(text=_("User ID: {}").format(message.author.id))

        await self.send_log_msg(message.guild, embed, settings)

    async def message_spam_log(
        self, message: discord.Message, settings: dict, total: int
    ):
        """Send embed with message spam log message."""

        embed = discord.Embed(
            color=self.get_event_colour('message_spam', settings),
            description=message.content,
            timestamp=message.created_at
        )

        embed.add_field(
            name="Channel",
            value=message.channel.mention
        )

        embed.add_field(
            name="Reason",
            value=_("Message Spam ({} messages/{} secs)").format(
                settings['message_spam']['limit'],
                settings['automod_duration']
            )
        )

        embed.set_author(
            name=_("{} - {} Messages Deleted").format(message.author, total),
            icon_url=message.author.avatar_url
        )
        embed.set_footer(text=_("User ID: {}").format(message.author.id))

        await self.send_log_msg(message.guild, embed, settings)

    async def invite_log(
        self,
        message: discord.Message,
        settings: dict,
        invite: Union[discord.Invite, str]
    ):
        """Send embed with invite log message."""

        embed = discord.Embed(
            color=self.get_event_colour('filter_invites', settings),
            description=message.content,
            timestamp=message.created_at
        )

        embed.add_field(
            name="Channel",
            value=message.channel.mention
        )

        reason = _("Server Invite")
        if isinstance(invite, discord.Invite):
            reason += _(" (Code: `{}`, Server: `{}`)").format(
                invite.code, invite.guild.name
            )

        else:
            reason += _(" (Code: `{}`)").format(invite)

        embed.add_field(name="Reason",value=reason)

        embed.set_author(
            name=_("{} - Message Deleted").format(message.author),
            icon_url=message.author.avatar_url
        )
        embed.set_footer(text=_("User ID: {}").format(message.author.id))

        await self.send_log_msg(message.guild, embed, settings)

    async def filter_log(
        self, message: discord.Message, settings: dict, match: re.Match
    ):
        """Send embed with message filter log message."""

        embed = discord.Embed(
            color=self.get_event_colour('filter_messages', settings),
            description=message.content,
            timestamp=message.created_at
        )

        embed.add_field(
            name="Channel",
            value=message.channel.mention
        )

        reason = _("Filtered Word (Match: `{}`)").format(match.group())

        embed.add_field(name="Reason",value=reason)

        embed.set_author(
            name=_("{} - Message Deleted").format(message.author),
            icon_url=message.author.avatar_url
        )
        embed.set_footer(text=_("User ID: {}").format(message.author.id))

        await self.send_log_msg(message.guild, embed, settings)

    async def send_log_msg(
        self, guild: discord.Guild, embed: discord.Embed, settings: dict
    ):
        """Send message to the log."""

        log_id = settings['log_channel']
        if log_id:
            log_channel = guild.get_channel(log_id)
        else:
            log_channel = None

        if log:
            await log_channel.send(embed=embed)
        else:
            log.exception("Could not log automod event: log_channel was `None`.")

    def total_mentions(self, message: discord.Message):
        """Return total number of mentions, including role mentions.

        Duplicates are counted each time.
        """

        return len(message.raw_mentions) + len(message.raw_role_mentions)

    def get_event_colour(self, event: str, settings: dict) -> discord.Colour:
        """Return colour for the event."""

        defaults = {
            'mention_spam': discord.Colour.dark_orange(),
            'message_spam': discord.Colour.dark_red(),
            'attachment_spam': discord.Colour.magenta(),
            'filter_invites': discord.Colour.dark_magenta(),
            'filter_messages': discord.Colour.purple()
        }

        colour = settings[event]['colour']

        if colour:
            return discord.Colour(colour)
        else:
            return defaults[event]

    def cog_unload(self):
        pass

    __unload = cog_unload
