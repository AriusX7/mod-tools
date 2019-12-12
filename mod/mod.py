import asyncio
import json
import logging
import os
import re

from collections import namedtuple
from datetime import datetime, timedelta
from typing import cast, Optional, Union

import babel.localedata

from babel.core import Locale
from babel.numbers import format_decimal
from dateutil import relativedelta

# Discord
import discord

from redbot.cogs.admin.admin import Admin
from redbot.cogs.admin.converters import MemberDefaultAuthor
from redbot.cogs.mod.mod import Mod   # This is the actual mod cog
from redbot.cogs.mod.converters import RawUserIds
from redbot.core import Config, checks, commands, modlog
from redbot.core.i18n import get_locale, Translator, cog_i18n
from redbot.core.modlog import Case, create_case, get_modlog_channel
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.common_filters import filter_invites
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.mod import get_audit_reason, is_allowed_by_hierarchy, is_mod_or_superior
from redbot.core.utils.predicates import ReactionPredicate


log = logging.getLogger("red.extmod")

default_guild = {
    "mute_role_id": None,
    "mute_channel_id": None,
    "current_tempmutes": [],
    "uslowmodes": {},  # id of channel and duration where slowmode is active for guild
    "sticky_roles": [],
    "modmail": None
}

default_member = {
    "muted_until": False,
    "current_slowmodes": {},  # id of channel and timestamp where slowmode is active for user
    "sticky_roles": []
}

_ = T_ = Translator("Mod", __file__)

mute_fail = "Failed to mute user. Reason:"

GENERIC_FORBIDDEN = _(
    "I attempted to do something that Discord denied me permissions for."
    " Your command failed to successfully complete."
)

HIERARCHY_ISSUE_ADD = _(
    "I tried to add {role.name} to {member.display_name} but that role"
    " is higher than my highest role in the Discord hierarchy so I was"
    " unable to successfully add it. Please give me a higher role and "
    "try again."
)

HIERARCHY_ISSUE_REMOVE = _(
    "I tried to remove {role.name} from {member.display_name} but that role"
    " is higher than my highest role in the Discord hierarchy so I was"
    " unable to successfully remove it. Please give me a higher role and "
    "try again."
)

USER_HIERARCHY_ISSUE_ADD = _(
    "I tried to add {role.name} to {member.display_name} but that role"
    " is higher than your highest role in the Discord hierarchy so I was"
    " unable to successfully add it. Please get a higher role and "
    "try again."
)

USER_HIERARCHY_ISSUE_REMOVE = _(
    "I tried to remove {role.name} from {member.display_name} but that role"
    " is higher than your highest role in the Discord hierarchy so I was"
    " unable to successfully remove it. Please get a higher role and "
    "try again."
)

ROLE_USER_HIERARCHY_ISSUE = _(
    "I tried to edit {role.name} but that role"
    " is higher than your highest role in the Discord hierarchy so I was"
    " unable to successfully add it. Please get a higher role and "
    "try again."
)

# remove when 3.2 is out
def humanize_number(val: Union[int, float], override_locale=None) -> str:
    """
    Convert an int or float to a str with digit separators based on bot locale

    Parameters
    ----------
    val : Union[int, float]
        The int/float to be formatted.
    override_locale: Optional[str]
        A value to override the bots locale.

    Returns
    -------
    str
        locale aware formatted number.
    """
    return format_decimal(val, locale=get_babel_locale(override_locale))

def _get_babel_locale(red_locale: str) -> babel.core.Locale:
    supported_locales = babel.localedata.locale_identifiers()
    try:  # Handles cases where red_locale is already Babel supported
        babel_locale = Locale(*babel.parse_locale(red_locale))
    except (ValueError, babel.core.UnknownLocaleError):
        try:
            babel_locale = Locale(*babel.parse_locale(red_locale, sep="-"))
        except (ValueError, babel.core.UnknownLocaleError):
            # ValueError is Raised by `parse_locale` when an invalid Locale is given to it
            # Lets handle it silently and default to "en_US"
            try:
                # Try to find a babel locale that's close to the one used by red
                babel_locale = Locale(Locale.negotiate([red_locale], supported_locales, sep="-"))
            except (ValueError, TypeError, babel.core.UnknownLocaleError):
                # If we fail to get a close match we will then default to "en_US"
                babel_locale = Locale("en", "US")
    return babel_locale

def get_babel_locale(locale: Optional[str] = None) -> babel.core.Locale:
    """Function to convert a locale to a ``babel.core.Locale``.

    Parameters
    ----------
    locale : Optional[str]
        The locale to convert, if not specified it defaults to the bot's locale.

    Returns
    -------
    babel.core.Locale
        The babel locale object.
    """
    if locale is None:
        locale = get_locale()
    return _get_babel_locale(locale)


# This makes sure the cog name is "Mod" for help still.
@cog_i18n(_)
class ExtMod(Mod, name="Mod"):

    def __init__(self, bot):
        super().__init__(bot)
        self.config = Config.get_conf(
            self, 1_310_127_007, force_registration=True)

        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

        def error_callback(fut):
            try:
                fut.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logging.exception("Error in something", exc_info=exc)

        self.tmute_expiry_task = self.bot.loop.create_task(self.check_tempmute_expirations())
        self.uslow_expiry_task = self.bot.loop.create_task(self.check_uslow_expirations())
        self.tmute_expiry_task.add_done_callback(error_callback)
        self.uslow_expiry_task.add_done_callback(error_callback)

    async def initialize(self):
        await self.register_casetypes()

    @staticmethod
    async def register_casetypes():
        new_types = [
            {
                "name": "mute",
                "default_setting": True,
                "image": "\N{SPEAKER WITH CANCELLATION STROKE}",
                "case_str": "Mute",
                # audit_type should be omitted if the action doesn't show
                # up in the audit log.
                "audit_type": "overwrite_update",
            },
            {
                "name": "unmute",
                "default_setting": True,
                "image": "\N{SPEAKER}",
                "case_str": "Unmute",
                "audit_type": "overwrite_update"
            },
            {
                "name": "note",
                "default_setting": True,
                "image": "\N{DOUBLE EXCLAMATION MARK}",
                "case_str": "Note",
                "audit_type": "overwrite_update"
            },
            {
                "name": "tempmute",
                "default_setting": True,
                "image": "\N{ALARM CLOCK}\N{SPEAKER WITH CANCELLATION STROKE}",
                "case_str": "Temp Mute",
                "audit_type": "overwrite_update"
            },
            {
                "name": "tempunmute",
                "default_setting": True,
                "image": "\N{ALARM CLOCK}\N{SPEAKER}",
                "case_str": "Temp Unmute",
                "audit_type": "overwrite_update"
            },
        ]
        try:
            await modlog.register_casetypes(new_types)
        except RuntimeError:
            pass
    
    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @checks.mod_or_permissions(administrator=True)
    async def mute(self, ctx: commands.Context, user: discord.Member, 
        duration: Optional[str] = None, *, reason: Optional[str] = None):
        """Mute a user.

        If a reason is specified, it will be the reason that shows up
        in the audit log.
        """

        guild = ctx.guild
        author = ctx.message.author

        is_mod = await is_mod_or_superior(bot=self.bot, obj=user)
        if  is_mod:
            return await ctx.send(f"{mute_fail} user has moderator or higher permissions.")

        # if user.top_role >= author.top_role:
        #     return await ctx.send(f"{mute_fail} user is higher in hierarchy.")

        # if user.top_role >= self.bot:
        #     return await ctx.send(f"{mute_fail} user is higher in hierarchy.")

        mute_role_id = await self.config.guild(guild).mute_role_id()
        mute_role = discord.utils.get(guild.roles, id=mute_role_id)

        mute_channel_id = await self.config.guild(guild).mute_channel_id()
        mute_channel = discord.utils.get(guild.roles, id=mute_channel_id)

        # check if muted role is set
        if not mute_role:
            # retrieves muted role if it exists, returns none if there isn't.
            mute_role = discord.utils.get(guild.roles, name="Muted")

            if not mute_role:
                msg = await ctx.send("Muted role not set. Create a new muted role?")
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                await ctx.bot.wait_for("reaction_add", check=pred)
                if pred.result is True:
                    try:  # creates muted role
                        muted = await guild.create_role(name="Muted", reason="To use for muting")
                        for channel in guild.channels:  # removes permission to send messages, add reactions and speak in VCs
                            await channel.set_permissions(muted, send_messages=False, add_reactions=False, speak=False)
                        mute_role = muted
                    except discord.Forbidden:
                        return await ctx.send("Insufficient permissions to make a role.")
                else:
                    return await ctx.send(f"{mute_fail} Muted role doesn't exist.")

            mute_role_id = mute_role.id
            # set mute role
            await self.config.guild(guild).mute_role_id.set(mute_role_id)
            # add the muted role to server sticky roles
            async with self.config.guild(guild).sticky_roles() as sticky_roles:
                sticky_roles.append(mute_role.id)

        if not mute_channel:
            # retrieves muted channel if it exists, returns none if there isn't.
            mute_channel = discord.utils.get(guild.channels, name="muted")

            if not mute_channel:
                msg = await ctx.send("Muted channel not set. Create a new muted channel?")
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                await ctx.bot.wait_for("reaction_add", check=pred)
                if pred.result is True:
                    try:  # creates muted channel
                        overwrites = {
                            guild.default_role: discord.PermissionOverwrite(read_messages=False),
                            mute_role: discord.PermissionOverwrite(read_messages=True, send_messages=False, add_reactions=False)
                        }

                        muted = await guild.create_text_channel('muted', overwrites=overwrites)
                        mute_channel = muted
                    except discord.Forbidden:
                        return await ctx.send("Insufficient permissions to make a channel.")
                else:
                    return await ctx.send(f"{mute_fail} Muted channel doesn't exist.")

            mute_channel_id = mute_channel.id
            # set mute channel
            await self.config.guild(guild).mute_channel_id.set(mute_channel_id)

        temp = False
        if duration or reason:
            if re.match(r"^\d+\s*[d,h,m,s]", duration):
                duration_str = self.get_time(duration, ret_str=True)
                temp = True
            else:
                duration_str = "indefinitely"
                reason = f"{duration} {reason}".strip() if reason else duration
        else:
            duration_str = "indefinitely"

        if temp:
            try:
                d, h, m, s = self.get_time(duration)
            except ValueError:
                return await ctx.send("Invalid duration format.")
            delta = timedelta(days=d, hours=h, minutes=m, seconds=s)
            unmute_time = datetime.utcnow() + delta
            mute_type = "tempmute"
        else:
            unmute_time = None
            mute_type = "mute"
        # await user.add_roles(mute_role) # adds muted role
        success, issue = await self._mute(ctx, user, unmute_time)
        if issue:
            return await ctx.send(issue)

        next_case_no = await modlog.get_next_case_number(guild)  # actually the current case no

        await ctx.send(f"Muted **{user}** {duration_str.strip()}."
            f" User notified in {mute_channel.mention}. (Case number {next_case_no}) ")

        modmail = await self.config.guild(guild).modmail()
        if modmail:
            modmail_user = discord.utils.get(guild.members, id=modmail)
            await mute_channel.send(f"{user.mention} You have been muted {duration_str.strip()}."
                f"{f' Reason given: {reason}.' if reason else ''} If you'd like to appeal, send a"
                    f" DM to {modmail_user.mention} or an online moderator.")
        else:
            await mute_channel.send(f"{user.mention} You have been muted {duration_str.strip()}."
                f"{f' Reason given: {reason}.' if reason else ''} If you'd like to appeal, send a"
                    f" DM to an online moderator.")

        # create modlog entry 
        try:
            await modlog.create_case(
                self.bot,
                guild,
                ctx.message.created_at,
                mute_type,
                user,
                author,
                reason,
                until=unmute_time,
                channel=None,
            )
        except RuntimeError as e:
            await ctx.send(e)

    @mute.command(name="role")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def set_muted_role(self, ctx: commands.Context, role: discord.Role):
        """Set muted role."""

        role_id = role.id
        await self.config.guild(ctx.guild).mute_role_id.set(role_id)

        # add the muted role to server sticky roles
        async with self.config.guild(ctx.guild).sticky_roles() as sticky_roles:
            sticky_roles.append(role_id)
            
        await ctx.send(f"Set {role.mention} as the muted role!")

    @mute.command(name="channel")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def set_muted_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set muted channel."""

        channel_id = channel.id
        await self.config.guild(ctx.guild).mute_channel_id.set(channel_id)
        await ctx.send(f"Set {channel.mention} as the muted channel!")

    @commands.command(name="modmail")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def _modmail(self, ctx: commands.Context, modmail: discord.Member):
        """Set server's modmail.
        
        Replaces old, if any, setting.
        """

        await self.config.guild(ctx.guild).modmail.set(modmail.id)
        await ctx.send(f"Set **{modmail}** as the server's modmail.")
    
    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @checks.mod_or_permissions(administrator=True)
    async def unmute(self, ctx: commands.Context, *, user: discord.Member):
        """Unmute a user."""

        is_mod = await is_mod_or_superior(bot=self.bot, obj=user)
        if  is_mod:
            return await ctx.send(f"{mute_fail} user has moderator permissions.")

        mute_role_id = await self.config.guild(ctx.guild).mute_role_id()
        if mute_role_id not in [y.id for y in user.roles]:
            return await ctx.send(f"{user} is not muted.")

        mute_role = discord.utils.get(ctx.guild.roles, id=mute_role_id)
        await user.remove_roles(mute_role)

        async with self.config.member(user).sticky_roles() as sticky_roles:
            sticky_roles.remove(mute_role_id)

        await ctx.send(f"Unmuted **{user}**.")

        try:
            await modlog.create_case(
                self.bot,
                ctx.guild,
                ctx.message.created_at,
                "unmute",
                user,
                ctx.author,
                reason=None,
                until=None,
            )
        except RuntimeError as e:
            await ctx.send(e)
    
    @commands.command(aliases=["ui"])
    @commands.bot_has_permissions(embed_links=True)
    async def userinfo(self, ctx: commands.Context, *, user: discord.User = None):
        """Show information about a user.

        This includes fields for status, discord join date, server
        join date, voice state, previous names/nicknames and case history.

        If the user has no roles, previous names or previous nicknames or case history,
        these fields will be omitted.
        """
        author = ctx.author
        guild = ctx.guild

        if not user:
            user = author
        
        joined_at = user.joined_at
        since_created = (ctx.message.created_at - user.created_at).days
        if joined_at is not None:
            since_joined = (ctx.message.created_at - joined_at).days
            user_joined = joined_at.strftime("%d %b %Y %H:%M")
        else:
            since_joined = "?"
            user_joined = _("Unknown")
        user_created = user.created_at.strftime("%d %b %Y %H:%M")

        created_on = _("{}\n({} days ago)").format(user_created, since_created)
        joined_on = _("{}\n({} days ago)").format(user_joined, since_joined)

        activity = _("Chilling in {} status").format(user.status)
        if user.activity is None:  # Default status
            pass
        elif user.activity.type == discord.ActivityType.playing:
            activity = _("Playing {}").format(user.activity.name)
        elif user.activity.type == discord.ActivityType.streaming:
            activity = _("Streaming [{}]({})").format(user.activity.name, user.activity.url)
        elif user.activity.type == discord.ActivityType.listening:
            activity = _("Listening to {}").format(user.activity.name)
        elif user.activity.type == discord.ActivityType.watching:
            activity = _("Watching {}").format(user.activity.name)

        data = discord.Embed(description=activity, colour=user.colour)
        data.add_field(name=_("Joined Discord on"), value=created_on)
        data.add_field(name=_("Joined this server on"), value=joined_on)

        if guild:
            roles = user.roles[-1:0:-1]
            names, nicks = await self.get_names_and_nicks(user)
            cases = await self._cases_info(ctx, user)
            voice_state = user.voice
            member_number = (
                sorted(guild.members, key=lambda m: m.joined_at or ctx.message.created_at).index(user)
                + 1
            )

            if roles:

                role_str = ", ".join([x.mention for x in roles])
                # 400 BAD REQUEST (error code: 50035): Invalid Form Body
                # In embed.fields.2.value: Must be 1024 or fewer in length.
                if len(role_str) > 1024:
                    # Alternative string building time.
                    # This is not the most optimal, but if you're hitting this, you are losing more time
                    # to every single check running on users than the occasional user info invoke
                    # We don't start by building this way, since the number of times we hit this should be
                    # infintesimally small compared to when we don't across all uses of Red.
                    continuation_string = _(
                        "and {numeric_number} more roles not displayed due to embed limits."
                    )
                    available_length = 1024 - len(continuation_string)  # do not attempt to tweak, i18n

                    role_chunks = []
                    remaining_roles = 0

                    for r in roles:
                        chunk = f"{r.mention}, "
                        chunk_size = len(chunk)

                        if chunk_size < available_length:
                            available_length -= chunk_size
                            role_chunks.append(chunk)
                        else:
                            remaining_roles += 1

                    role_chunks.append(continuation_string.format(numeric_number=remaining_roles))

                    role_str = "".join(role_chunks)

            else:
                role_str = None

            if cases:
                
                initial_str = f"Total Cases: {len(cases)}\nSummary: "
                case_str = initial_str + ", ".join([f"{cases[x]} (#{x})" for x in cases])

                # 400 BAD REQUEST (error code: 50035): Invalid Form Body
                # In embed.fields.3.value: Must be 1024 or fewer in length.
                if len(case_str) > 1024:
                    # Alternative string building time.
                    # This is not the most optimal, but if you're hitting this, you are losing more time
                    # to every single check running on users than the occasional user info invoke
                    # We don't start by building this way, since the number of times we hit this should be
                    # infintesimally small compared to when we don't across all uses of Red.
                    continuation_string = _(
                        "and {numeric_number} more cases not displayed due to embed limits."
                    )
                    available_length = 1024 - len(initial_str) - len(continuation_string)  # do not attempt to tweak, i18n

                    case_chunks = []
                    remaining_cases = 0

                    for r in roles:
                        chunk = f"{r.mention}, "
                        chunk_size = len(chunk)

                        if chunk_size < available_length:
                            available_length -= chunk_size
                            case_chunks.append(chunk)
                        else:
                            remaining_cases += 1

                    case_chunks.append(continuation_string.format(numeric_number=remaining_cases))

                    case_str = initial_str + "".join(case_chunks)

            else:
                case_str = None

            if role_str is not None:
                data.add_field(name=_("Roles"), value=role_str, inline=False)
            if names:
                # May need sanitizing later, but mentions do not ping in embeds currently
                val = filter_invites(", ".join(names))
                data.add_field(name=_("Previous Names"), value=val, inline=False)
            if nicks:
                # May need sanitizing later, but mentions do not ping in embeds currently
                val = filter_invites(", ".join(nicks))
                data.add_field(name=_("Previous Nicknames"), value=val, inline=False)
            if voice_state and voice_state.channel:
                data.add_field(
                    name=_("Current voice channel"),
                    value="{0.mention} ID: {0.id}".format(voice_state.channel),
                    inline=False,
                )
            if case_str is not None:
                data.add_field(name=_("Cases"), value=case_str, inline=False)
            data.set_footer(text=_("Member #{} | User ID: {}").format(member_number, user.id))

        name = str(user)
        name = " ~ ".join((name, user.nick)) if user.nick else name
        name = filter_invites(name)

        if user.avatar:
            avatar = user.avatar_url_as(static_format="png")
            data.set_author(name=name, url=avatar)
            data.set_thumbnail(url=avatar)
        else:
            data.set_author(name=name)

        await ctx.send(embed=data)

    @commands.command(name="note")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def note(self, ctx: commands.Context, user: discord.Member, *, note: str = None):
        """Add a note to a user."""

        if not note:
            return await ctx.send("A note is required!")
        
        try:
            await modlog.create_case(
                self.bot,
                ctx.guild,
                ctx.message.created_at,
                "note",
                user,
                ctx.author,
                reason=note,
                until=None,
            )
        except RuntimeError as e:
            await ctx.send(e)
        
        await ctx.send(f"Added note to **{user}**.")
    
    @commands.command(name="cases")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def cases(self, ctx: commands.Context, *, user: discord.Member):
        """Get case history of a user."""

        cases_info = await self._cases_info(ctx, user)
        if not cases_info:
            return await ctx.send(f"No cases registered for {user}.")
        
        case_str = ""
        for case in cases_info:
            case_str += f"**{case}:** {cases_info[case]}\n"

        await ctx.send(case_str)
    
    @commands.command(name="ban")
    @commands.guild_only()
    @commands.bot_has_permissions(ban_members=True)
    @checks.mod_or_permissions(administrator=True)
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.Member,
        days: Optional[int] = 0,
        *,
        reason: str = None
    ):  
        """Ban a user from this server and optionally delete days of messages.

        If days is not a number, it's treated as the first word of the reason.
        Minimum 0 days, maximum 7. Defaults to 0.
        """

        author = ctx.author
        guild = ctx.guild

        if author == user:
            return await ctx.send(_("I cannot let you do that. Self-harm is bad. {}")
                .format("\N{PENSIVE FACE}"))
        elif not await is_allowed_by_hierarchy(self.bot, self.settings, guild, author, user):
            return await ctx.send(_(
                "I cannot let you do that. You are "
                "not higher than the user in the role "
                "hierarchy."
            ))
        elif guild.me.top_role <= user.top_role or user == guild.owner:
            return await ctx.send(_("I cannot do that due to discord hierarchy rules"))
        elif not (0 <= days <= 7):
            return await ctx.send(_("Invalid days. Must be between 0 and 7."))

        audit_reason = get_audit_reason(author, reason)

        queue_entry = (guild.id, user.id)
        try:
            await guild.ban(user, reason=audit_reason, delete_message_days=days)
            log.info(
                "{}({}) banned {}({}), deleting {} days worth of messages".format(
                    author.name, author.id, user.name, user.id, str(days)
                )
            )
        except discord.Forbidden:
            return await ctx.send(_("I'm not allowed to do that."))
        except Exception as e:
            return e  # TODO: impproper return type? Is this intended to be re-raised?

        if days > 0:
            extra = f", deleting {days} days worth of messages. "
        else:
            extra = ". "

        next_case_no = await modlog.get_next_case_number(guild)  # actually the current case no
        await ctx.send(_(f"Banned **{user}** indefinitely"
            f"{extra}(Case number #{next_case_no})"))
        
        try:
            await modlog.create_case(
                self.bot,
                guild,
                ctx.message.created_at,
                "ban",
                user,
                author,
                reason,
                until=None,
                channel=None,
            )
        except RuntimeError as e:
            return await ctx.send(_(
                "The user was banned but an error occurred when trying to "
                "create the modlog entry: {reason}"
            ).format(reason=e))

    @commands.command(name="uslowmode", aliases=['uslow'])
    @commands.guild_only()
    @commands.bot_has_permissions(manage_channels=True)
    @checks.mod_or_permissions(administrator=True)
    async def _uslowmode(
        self, 
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        *,
        duration: Optional[str] = None
    ):
        """Set a custom slowmode over 6 hours in the channel.
        
        Interval can be anything from 0 seconds to 10 days. You should use `slowmode` 
        for anything upto 6 hours.
        Use without parameters to reset.
        """

        if not channel:
            channel = ctx.channel

        if duration:
            if not re.match(r"^\d+\s*[d,h,m,s]", duration):
                return await ctx.send("Invalid duration format.")

            try:
                d, h, m, s = self.get_time(duration)
            except ValueError:
                return await ctx.send("Invalid duration format.")

            interval = timedelta(days=d, hours=h, minutes=m, seconds=s)
            seconds = interval.total_seconds()
        
        else:
            seconds = 0

        if seconds <= 0:
            seconds = None

        async with self.config.guild(ctx.guild).uslowmodes() as uslowmodes:
            uslowmodes[channel.id] = seconds
        
        if seconds:
            await ctx.send(f"Set a custom slowmode {self.get_time(duration, ret_str=True)}"
                f"in {channel.mention}.")
        else:
            await ctx.send(f"Disabled custom slowmode in {channel.mention}.")

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(ban_members=True)
    @checks.mod_or_permissions(ban_members=True)
    async def softban(self, ctx: commands.Context, user: discord.Member, *, reason: str = None):
        """Kick a user and delete 1 day's worth of their messages."""
        
        guild = ctx.guild
        author = ctx.author

        if author == user:
            await ctx.send(
                _("I cannot let you do that. Self-harm is bad {emoji}").format(
                    emoji="\N{PENSIVE FACE}"
                )
            )
            return
        elif not await is_allowed_by_hierarchy(self.bot, self.settings, guild, author, user):
            await ctx.send(
                _(
                    "I cannot let you do that. You are "
                    "not higher than the user in the role "
                    "hierarchy."
                )
            )
            return

        audit_reason = get_audit_reason(author, reason)
        queue_entry = (guild.id, user.id)

        try:
            await guild.ban(user, reason=audit_reason, delete_message_days=1)
        except discord.errors.Forbidden:
            await ctx.send(_("My role is not high enough to softban that user."))
            return
        except discord.HTTPException as e:
            print(e)
            return
        try:
            await guild.unban(user)
        except discord.HTTPException as e:
            print(e)
            return
        else:
            log.info(
                "{}({}) softbanned {}({}), deleting 1 day worth "
                "of messages".format(author.name, author.id, user.name, user.id)
            )
            try:
                await modlog.create_case(
                    self.bot,
                    guild,
                    ctx.message.created_at,
                    "softban",
                    user,
                    author,
                    reason,
                    until=None,
                    channel=None,
                )
            except RuntimeError as e:
                await ctx.send(e)

            next_case_no = await modlog.get_next_case_number(guild) 
            await ctx.send(_(f"Softbanned **{user.name}**. (Case number {next_case_no - 1})"))
  
    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(ban_members=True)
    @checks.admin_or_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = None):
        """Unban a user from this server.

        Requires specifying the target user's ID. To find this, you may either:
         1. Copy it from the mod log case (if one was created), or
         2. enable developer mode, go to Bans in this server's settings, right-
        click the user and select 'Copy ID'.
        """

        guild = ctx.guild
        author = ctx.author

        try:
            user = await self.bot.fetch_user(user_id)
        except discord.errors.NotFound:
            await ctx.send(_("Couldn't find a user with that ID!"))
            return
            
        audit_reason = get_audit_reason(ctx.author, reason)
        bans = await guild.bans()
        bans = [be.user for be in bans]
        if user not in bans:
            await ctx.send(_("It seems that user isn't banned!"))
            return
        queue_entry = (guild.id, user.id)
        try:
            await guild.unban(user, reason=audit_reason)
        except discord.HTTPException:
            await ctx.send(_("Something went wrong while attempting to unban that user"))
            return
        else:
            try:
                await modlog.create_case(
                    self.bot,
                    guild,
                    ctx.message.created_at,
                    "unban",
                    user,
                    author,
                    reason,
                    until=None,
                    channel=None,
                )
            except RuntimeError as e:
                await ctx.send(e)
            next_case_no = await modlog.get_next_case_number(guild) 
            await ctx.send(_(f"Unbanned **{user}** from the server. (Case number {next_case_no - 1})"))

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(ban_members=True)
    @checks.admin_or_permissions(ban_members=True)
    async def hackban(
        self,
        ctx: commands.Context,
        user_ids: commands.Greedy[RawUserIds],
        days: Optional[int] = 0,
        *,
        reason: str = None,
    ):
        """Preemptively bans user(s) from the server

        User IDs need to be provided in order to ban
        using this command
        """

        days = cast(int, days)
        banned = []
        errors = {}

        async def show_results():
            text = ""
            if len(banned) > 0:
                text += _("Banned **{users}** from the server.").format(
                    users=', '.join([str(item) for item in banned])
                )

                next_case_no = await modlog.get_next_case_number(guild)  # the current case no + len(banned)
                next_case_no -= len(banned)

                if len(banned) > 1:
                    case_no_str = " (Case numbers "
                else:
                    case_no_str = " (Case number "

                numbers = [str(next_case_no + i) for i in range(len(banned))]
                case_no_str += ', '.join(numbers)
                case_no_str += ")"

                text += case_no_str

            if errors:
                text += _("\n**Errors:**\n")
                text += "\n".join(errors.values())

            for p in pagify(text):
                await ctx.send(p)

        def remove_processed(ids):
            return [_id for _id in ids if _id not in banned and _id not in errors]

        user_ids = list(set(user_ids))  # No dupes

        author = ctx.author
        guild = ctx.guild

        if not user_ids:
            await ctx.send_help()
            return

        if not (0 <= days <= 7):
            await ctx.send(_("Invalid days. Must be between 0 and 7."))
            return

        if not guild.me.guild_permissions.ban_members:
            return await ctx.send(_("I lack the permissions to do this."))

        ban_list = await guild.bans()
        for entry in ban_list:
            for user_id in user_ids:
                if entry.user.id == user_id:
                    errors[user_id] = _("User {user_id} is already banned.").format(
                        user_id=user_id
                    )

        user_ids = remove_processed(user_ids)

        if not user_ids:
            await show_results()
            return

        for user_id in user_ids:
            user = guild.get_member(user_id)
            if user is not None:
                # Instead of replicating all that handling... gets attr from decorator
                try:
                    result = await self.ban_user(
                        user=user, ctx=ctx, days=days, reason=reason, create_modlog_case=True
                    )
                    if result is True:
                        banned.append(user_id)
                    else:
                        errors[user_id] = _("Failed to ban user {user_id}: {reason}").format(
                            user_id=user_id, reason=result
                        )
                except Exception as e:
                    errors[user_id] = _("Failed to ban user {user_id}: {reason}").format(
                        user_id=user_id, reason=e
                    )

        user_ids = remove_processed(user_ids)

        if not user_ids:
            await show_results()
            return

        for user_id in user_ids:
            user = discord.Object(id=user_id)
            audit_reason = get_audit_reason(author, reason)
            queue_entry = (guild.id, user_id)
            try:
                await guild.ban(user, reason=audit_reason, delete_message_days=days)
                log.info("{}({}) hackbanned {}".format(author.name, author.id, user_id))
            except discord.NotFound:
                errors[user_id] = _("User {user_id} does not exist.").format(user_id=user_id)
                continue
            except discord.Forbidden:
                errors[user_id] = _("Could not ban {user_id}: missing permissions.").format(
                    user_id=user_id
                )
                continue
            else:
                banned.append(user_id)

            user_info = await self.bot.fetch_user(user_id)

            try:
                await modlog.create_case(
                    self.bot,
                    guild,
                    ctx.message.created_at,
                    "hackban",
                    user_info,
                    author,
                    reason,
                    until=None,
                    channel=None,
                )
            except RuntimeError as e:
                errors["0"] = _("Failed to create modlog case: {reason}").format(reason=e)
        await show_results()

    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(ban_members=True)
    @checks.admin_or_permissions(ban_members=True)
    async def tempban(
        self, ctx: commands.Context, user: discord.Member, days: int = 1, *, reason: str = None
    ):
        """Temporarily ban a user from this server."""

        guild = ctx.guild
        author = ctx.author
        days_delta = timedelta(days=int(days))
        unban_time = datetime.utcnow() + days_delta

        queue_entry = (guild.id, user.id)
        await self.settings.member(user).banned_until.set(unban_time.timestamp())
        cur_tbans = await self.settings.guild(guild).current_tempbans()
        cur_tbans.append(user.id)
        await self.settings.guild(guild).current_tempbans.set(cur_tbans)

        try:
            await guild.ban(user)
        except discord.Forbidden:
            await ctx.send(_("I can't do that for some reason."))
        except discord.HTTPException:
            await ctx.send(_("Something went wrong while banning"))
        else:
            try:
                await modlog.create_case(
                    self.bot,
                    guild,
                    ctx.message.created_at,
                    "tempban",
                    user,
                    author,
                    reason,
                    unban_time,
                )
            except RuntimeError as e:
                await ctx.send(e)

            day_str = f"{days} day"
            if days >= 1:
                day_str += "s"

            next_case_no = await modlog.get_next_case_number(guild)
            await ctx.send(_(f"Banned **{user}** for {day_str}. (Case number {next_case_no - 1})"))

    @commands.command(name="search")
    @commands.guild_only()
    @commands.mod_or_permissions(administrator=True)
    async def search(self, ctx: commands.Context, *, username: str):
        """Search for server users."""
        
        if '-' in username:
            flag = '-'.join(username.split('-')[1:])
            username = username.split('-')[0].strip()
        else:
            flag = None

        flags = ["cs", "case-sensitive", "file", "f", "b", "bot", "csf", "csb", "fcs", "bcs", "fb", "bf"]
        if flag:
            if flag.lower() not in flags:
                username += f" {flag}"
        
        cs = False
        if flag in ["cs", "case-sensitive"]:
            cs = True

        a_file = False
        if flag in ["file", "f"]:
            a_file = True

        bot = False
        if flag in ["bot", "b"]:
            bot = True

        if flag in ["csf", "fcs"]:
            cs = a_file = True
        
        if flag in ["csb", "bcs"]:
            cs = bot = True

        if flag in ["bf", "fb"]:
            bot = a_file = True

        matches = []

        for user in ctx.guild.members:
            cond = False
            if cs:
                if bot and not user.bot:
                    continue
                if user.nick:
                    cond = True if username in user.nick else False
                if username in user.name or cond:
                    matches.append(user)
            else:
                if bot and not user.bot:
                    continue
                if user.nick:
                    cond = True if username in user.nick.lower() else False
                if username.lower() in user.name.lower() or cond:
                    matches.append(user)
        
        if not matches:
            return await ctx.send("No match found.")

        await ctx.send(f"{len(matches)} matches found.")

        _matches = matches[:10]
        if len(matches) > 10:
            # only return first 10 matches
            await ctx.send(f"Showing first 10 matches. Whole result can be found in the file below.")
            a_file = True

        match_str = "```rust"
        if a_file:
            f = open("search_results.txt", "w")
        for match in _matches:
            if isinstance(match, discord.Member):
                if match.nick:
                    nick = f" ({match.nick})"
                else:
                    nick = ""
                match_str += f"\n{match.id} {match}{nick}"
                if a_file:
                    f.write(f"{match.id}\t{match}{nick}\n")
        match_str += "```"

        await ctx.send(match_str)

        if a_file:
            f.close()
            f = open("search_results.txt", "r")
            await ctx.send(file=discord.File(f, 'search_results.txt'))
            f.close()
            try:
                # delete file after sending
                os.remove('search_results.txt')
            except OSError:
                # remove all the text if unable to delete file
                open('search_results.txt', 'w').close()
    
    @commands.command(name="bans")
    @commands.guild_only()
    @commands.bot_has_permissions(view_audit_log=True)
    @checks.mod_or_permissions(administrator=True)
    async def bans(self, ctx: commands.Context):
        """List of all active server bans."""

        guild = ctx.guild

        ban_data = []

        ban_list = await guild.bans()
        for idx, entry in enumerate(ban_list):
            ban_data.append({
                "index": idx+1,
                "user": f"{entry.user}",
                "user_id": entry.user.id,
                "reason": entry.reason
            })
        
        f = open("bans.json", "w")
        json.dump(ban_data, f, indent=4, ensure_ascii=False)
        f.close()

        # f = open("bans.txt", "r")
        await ctx.send(f"{len(ban_list)} users are currently banned.")
        await ctx.send(file=discord.File('bans.json'))
        f.close()

        try:
            # delete file after sending
            os.remove('bans.json')
        except OSError:
            # remove all the text if unable to delete file
            open('bans.json', 'w').close()
     
    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(kick_members=True)
    @checks.admin_or_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, user: discord.Member, *, reason: str = None):
        """Kick a user.

        If a reason is specified, it will be the reason that shows up
        in the audit log.
        """

        author = ctx.author
        guild = ctx.guild

        if author == user:
            await ctx.send(
                _("I cannot let you do that. Self-harm is bad {emoji}").format(
                    emoji="\N{PENSIVE FACE}"
                )
            )
            return
        elif not await is_allowed_by_hierarchy(self.bot, self.settings, guild, author, user):
            await ctx.send(
                _(
                    "I cannot let you do that. You are "
                    "not higher than the user in the role "
                    "hierarchy."
                )
            )
            return
        elif ctx.guild.me.top_role <= user.top_role or user == ctx.guild.owner:
            await ctx.send(_("I cannot do that due to discord hierarchy rules"))
            return
        audit_reason = get_audit_reason(author, reason)
        try:
            await guild.kick(user, reason=audit_reason)
            log.info("{}({}) kicked {}({})".format(author.name, author.id, user.name, user.id))
        except discord.errors.Forbidden:
            await ctx.send(_("I'm not allowed to do that."))
        except Exception as e:
            print(e)
        else:
            try:
                await modlog.create_case(
                    self.bot,
                    guild,
                    ctx.message.created_at,
                    "kick",
                    user,
                    author,
                    reason,
                    until=None,
                    channel=None,
                )
            except RuntimeError as e:
                await ctx.send(e)
            next_case_no = await modlog.get_next_case_number(guild)
            await ctx.send(_(f"Kicked **{user}** from the server. (Case number {next_case_no - 1})"))
    
    @commands.command()
    @commands.guild_only()
    async def server(self, ctx: commands.Context):
        """Show information about server"""

        guild = ctx.guild

        online = humanize_number(
            len([m.status for m in guild.members if m.status == discord.Status.online])
        )
        idle = humanize_number(
            len([m.status for m in guild.members if m.status == discord.Status.idle])
        )
        dnd = humanize_number(
            len([m.status for m in guild.members if m.status == discord.Status.dnd])
        )
        offline = humanize_number(
            len([m.status for m in guild.members if m.status == discord.Status.offline])
        )

        total_users = humanize_number(len(guild.members))
        text_channels = humanize_number(len(guild.text_channels))
        voice_channels = humanize_number(len(guild.voice_channels))
        categories = humanize_number(len(guild.categories))
        roles = humanize_number(len(guild.roles))
        emojis = humanize_number(len(guild.emojis))

        created_at = guild.created_at
        now = datetime.utcnow()

        difference = relativedelta.relativedelta(now, created_at)

        years = difference.years
        months = difference.months
        weeks = difference.weeks
        days = difference.days

        created_at_strs = []
        if years:
            created_at_strs.append(f"{years} {'years' if years > 1 else 'year'}")
        if months:
            created_at_strs.append(f"{months} {'months' if months > 1 else 'month'}")
        if weeks:
            created_at_strs.append(f"{weeks} {'weeks' if weeks > 1 else 'week'}")
        if days and not weeks:
            created_at_strs.append(f"{days} {'days' if days > 1 else 'day'}")
        
        created_at_str = "**"
        created_at_str += ", ".join(created_at_strs) 
        created_at_str += f" ago** ({created_at.strftime('%d %b %Y %H:%M')})"

        desc = f"Created: {created_at_str}\nOwner: **{ctx.guild.owner}** ({ctx.guild.owner.id})"
        desc += f"\nVoice Region: **{guild.region}**"

        user_str = f"Total: **{total_users}**\nOnline: **{online}**\nIdle: **{idle}**"
        user_str += f"\nDND: **{dnd}**\nOffline: **{offline}**"

        other_stats = (
            f"Categories: **{categories}**\nText Channels: **{text_channels}**\nVoice Channels:"
            f" **{voice_channels}**\nRoles: **{roles}**\nBoost Level: **{guild.premium_tier}**"
        )

        data = discord.Embed(description=desc, colour=(await ctx.embed_colour()))

        data.add_field(name=_("\u200b\nMember Stats"), value=user_str)
        data.add_field(name=_("\u200b\nOthers"), value=other_stats)

        data.set_footer(text=_("Server ID: ") + str(guild.id))

        if guild.icon_url:
            data.set_author(name=f"About {guild.name}", url=guild.icon_url)
            data.set_thumbnail(url=guild.icon_url)
        else:
            data.set_author(name=f"About {guild.name}")

        try:
            await ctx.send(embed=data)
        except discord.Forbidden:
            await ctx.send(_("I need the `Embed links` permission to send this."))
    
    @commands.command(name="nickname", aliases=["nick"])
    @commands.guild_only()
    @commands.bot_has_permissions(manage_nicknames=True)
    @checks.mod_or_permissions(manage_nicknames=True)
    async def nick(self, ctx: commands.Context, user: discord.Member, *, nick: str = None):
        """Set or reset a user's nickname.
        
        Leaving the nick blank will reset it.
        """

        if not isinstance(user, discord.Member):
            return await ctx.send("Can't identify user!")

        try:
            await user.edit(nick=nick)
        except:
            return await ctx.send("Could not change user's nickname for some reason.")
        
        if nick:
            await ctx.send(f"Changed {user}'s nickname to **{nick}**!")
        else:
            await ctx.send(f"Reset **{user}'s** nickname!")
    
    @commands.command(name="cmute")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def channel_mute(
        self, 
        ctx: commands.Context, 
        user: discord.Member, 
        channel: Optional[discord.TextChannel] = None,
        *,
        reason: str = None
    ):
        """Mute a user from a channel (default to current)."""
    
    @commands.command(name="sticky")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def sticky_role(self, ctx: commands.Context, role: discord.Role):
        """Make a role sticky"""

        async with self.config.guild(ctx.guild).sticky_roles() as sticky_roles:
            if role.id not in sticky_roles:
                sticky_roles.append(role.id)
            else:
                return await ctx.send(f"**{role.name}** is already a sticky role!")

        await ctx.send(f"Changed **{role.name}** into a sticky role.")

    @commands.command(name="unsticky")
    @commands.guild_only()
    @checks.admin_or_permissions(administrator=True)
    async def unsticky_role(self, ctx: commands.Context, role: discord.Role):
        """Make a role non-sticky"""

        async with self.config.guild(ctx.guild).sticky_roles() as sticky_roles:
            if role.id in sticky_roles:
                sticky_roles.remove(role.id)
            else:
                return await ctx.send(f"**{role.name}** is not a sticky role!")

        await ctx.send(f"Changed **{role.name}** into a non-sticky role.")

    @commands.group(name="role")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def _role(self, ctx: commands.Context):
        """Add/remove roles to/from a user."""
        pass

    @_role.command(name="add")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def role_add(
        self, ctx: commands.Context, user: Optional[discord.Member] = None, *, role: discord.Role
    ):
        """Add a role to a user.

        If user is left blank it defaults to the author of the command.
        """
        if user is None:
            user = ctx.author

        admin = Admin(self.config)

        if admin.pass_user_hierarchy_check(ctx, role):
            try:
                await user.add_roles(role)
            except discord.Forbidden:
                if not self.pass_hierarchy_check(ctx, role):
                    await self.complain(ctx, T_(HIERARCHY_ISSUE_ADD), role=role, member=user)
                else:
                    await self.complain(ctx, T_(GENERIC_FORBIDDEN))
            else:
                await ctx.send(
                    _("Added **{role.name}** to **{member.display_name}**.").format(
                        role=role, member=user
                    )
                )
        else:
            await admin.complain(ctx, T_(USER_HIERARCHY_ISSUE_ADD), member=user, role=role)

    @_role.command(name="remove")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def role_remove(
        self, ctx: commands.Context, user: Optional[discord.Member] = None, *, role: discord.Role
    ):
        """Remove a role from a user.

        If user is left blank it defaults to the author of the command.
        """
        if user is None:
            user = ctx.author

        admin = Admin(self.config)

        if admin.pass_user_hierarchy_check(ctx, role):
            try:
                await user.remove_roles(role)
            except discord.Forbidden:
                if not self.pass_hierarchy_check(ctx, role):
                    await self.complain(ctx, T_(HIERARCHY_ISSUE_REMOVE), role=role, member=user)
                else:
                    await self.complain(ctx, T_(GENERIC_FORBIDDEN))
            else:
                await ctx.send(
                    _("Removed **{role.name}** from **{member.display_name}**.").format(
                        role=role, member=user
                    )
                )
        else:
            await admin.complain(ctx, T_(USER_HIERARCHY_ISSUE_ADD), member=user, role=role)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        author = message.author
        channel = message.channel

        leave = False
        
        is_mod = await is_mod_or_superior(bot=self.bot, obj=message)
        if  is_mod:
            leave = True

        if author == self.bot.user:
            leave = True

        if not leave:
            Member = namedtuple("Member", "id guild")

            async with self.config.guild(message.guild).uslowmodes() as uslowmodes:
                if str(channel.id) in uslowmodes.keys():
                    if uslowmodes[str(channel.id)]:
                        await channel.set_permissions(author, send_messages=False, add_reactions=False)

                    async with self.config.member(author).current_slowmodes() as slowmodes:
                        
                        timestamp = self.utc_timestamp(datetime.utcnow())

                        slowmodes[channel.id] = timestamp

    # remove when 3.2 is out
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member):

        if not guild.me.guild_permissions.view_audit_log:
            return

        try:
            await get_modlog_channel(guild)
        except RuntimeError:
            return  # No modlog channel so no point in continuing

        when = datetime.utcnow()
        before = when + timedelta(minutes=1)
        after = when - timedelta(minutes=1)
        await asyncio.sleep(10)  # prevent small delays from causing a 5 minute delay on entry

        attempts = 0
        # wait up to an hour to find a matching case
        while attempts < 12 and guild.me.guild_permissions.view_audit_log:
            attempts += 1
            try:
                entry = await guild.audit_logs(
                    action=discord.AuditLogAction.ban, before=before, after=after
                ).find(lambda e: e.target.id == member.id and after < e.created_at < before)
            except discord.Forbidden:
                break
            except discord.HTTPException:
                pass
            else:
                if entry:
                    if entry.user.id != guild.me.id:
                        # Don't create modlog entires for the bot's own bans, cogs do this.
                        mod, reason, date = entry.user, entry.reason, entry.created_at
                        await create_case(self.bot, guild, date, "ban", member, mod, reason)
                    return

            await asyncio.sleep(300)
    
    # remove when 3.2 is out 
    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if not guild.me.guild_permissions.view_audit_log:
            return

        try:
            await get_modlog_channel(guild)
        except RuntimeError:
            return  # No modlog channel so no point in continuing

        when = datetime.utcnow()
        before = when + timedelta(minutes=1)
        after = when - timedelta(minutes=1)
        await asyncio.sleep(10)  # prevent small delays from causing a 5 minute delay on entry

        attempts = 0
        # wait up to an hour to find a matching case
        while attempts < 12 and guild.me.guild_permissions.view_audit_log:
            attempts += 1
            try:
                entry = await guild.audit_logs(
                    action=discord.AuditLogAction.unban, before=before, after=after
                ).find(lambda e: e.target.id == user.id and after < e.created_at < before)
            except discord.Forbidden:
                break
            except discord.HTTPException:
                pass
            else:
                if entry:
                    if entry.user.id != guild.me.id:
                        # Don't create modlog entires for the bot's own unbans, cogs do this.
                        mod, reason, date = entry.user, entry.reason, entry.created_at
                        await create_case(self.bot, guild, date, "unban", user, mod, reason)
                    return

            await asyncio.sleep(300)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):

        guild = member.guild

        # check for sticky roles 
        async with self.config.member(member).sticky_roles() as sticky_roles:
            for role_id in sticky_roles:
                role = discord.utils.get(guild.roles, id=role_id)
                try:
                    await member.add_roles(role)
                except:
                    continue
    
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        
        guild = member.guild
        member_roles = member.roles

        # print(True)

        async with self.config.guild(guild).sticky_roles() as server_sticky_roles:
            # print(True)
            for sticky_role_id in server_sticky_roles:
                for member_role in member_roles:
                    if sticky_role_id == member_role.id:
                        async with self.config.member(member).sticky_roles() as sticky_roles:
                            if sticky_role_id not in sticky_roles: 
                                sticky_roles.append(sticky_role_id)

        async with self.config.member(member).sticky_roles() as sticky_roles:
            for sticky_role_id in sticky_roles:
                if sticky_role_id not in server_sticky_roles: 
                    # sticky_roles.remove(sticky_role_id)
                    # sticky_roles.remove(560444582429589504)
                    sticky_roles[:] = [i for i in sticky_roles if i != sticky_role_id]
    
    async def _cases_info(self, ctx: commands.Context, user: discord.Member):
        """Get case summary of a member."""
        
        user_cases = await modlog.get_cases_for_member(
            bot=self.bot, guild=ctx.guild, member=user
        )
        
        if not user_cases:
            return False

        cases_info = {}
        for case in user_cases:
            case_json = case.to_json()
            case_no = case_json["case_number"]
            case_type = await modlog.get_casetype(case_json["action_type"])
            case_name = case_type.case_str
            # if "mute" in case_name.lower():
            #     case_name = "Mute"
            # case_type_name = modlog.get_casetype(case_type)
            cases_info[case_no] = case_name
            # await ctx.send(f"**Case Number:** {case_no}\n**Case Type:** {case_type.case_str}")
        return cases_info
    
    async def _mute(self, ctx: commands.Context, user: discord.Member, dur: datetime = None):
        """Add/remove mute role. This is a separate function to support temporary mutes."""

        guild = ctx.guild
        
        mute_role_id = await self.config.guild(guild).mute_role_id()
        if mute_role_id in [y.id for y in user.roles]:
            return False, f"{user} is already muted."

        if dur:
            queue_entry = (guild.id, user.id)

            timestamp = self.utc_timestamp(dur)

            await self.config.member(user).muted_until.set(timestamp)
            cur_tmutes = await self.config.guild(guild).current_tempmutes()
            cur_tmutes.append(user.id)
            await self.config.guild(guild).current_tempmutes.set(cur_tmutes)
        
        mute_role = discord.utils.get(guild.roles, id=mute_role_id)

        await user.add_roles(mute_role)  # adds muted role
        async with self.config.member(user).sticky_roles() as sticky_roles:
            sticky_roles.append(mute_role_id)

        return True, False
    
    async def check_tempmute_expirations(self):
        Member = namedtuple("Member", "id guild")
        while True:
            for guild in self.bot.guilds:
                async with self.config.guild(guild).current_tempmutes() as guild_tempmutes:
                    for uid in guild_tempmutes.copy():
                        try:
                            unmute_time = datetime.utcfromtimestamp(
                                await self.config.member(Member(uid, guild)).muted_until()
                            )
                        except TypeError:
                            continue
                        if not unmute_time:
                            continue
                        if datetime.utcnow() > unmute_time: # time to unmute the user
                            user = await self.bot.fetch_user(uid)
                            member = discord.utils.get(guild.members, id=uid)
                            queue_entry = (guild.id, user.id)
                            try:
                                mute_role_id = await self.config.guild(guild).mute_role_id()
                                mute_role = discord.utils.get(guild.roles, id=mute_role_id)
                                await member.remove_roles(mute_role)
                                async with self.config.member(member).sticky_roles() as sticky_roles:
                                    sticky_roles.remove(mute_role_id)
                                guild_tempmutes.remove(uid)
                                await self.config.member(Member(uid, guild)).muted_until.set(None)
                                await self.edit_tmute_msg(guild=guild, user=member)
                                await self.create_temp_unmute_case(user, guild)

                            except discord.HTTPException as e:
                                # 50013: Missing permissions error code or 403: Forbidden status
                                log.info(
                                    f"Failed to unmute {user}({user.id})"
                                    f" from {guild.name}({guild.id}) due to permissions."
                                )
            await asyncio.sleep(60)
    
    async def edit_tmute_msg(self, guild: discord.Guild, user: discord.Member):
        """Edit the temporary mute modlog message when unmuting."""
        user_cases = await modlog.get_cases_for_member(guild=guild, bot=self.bot, member=user)
        
        case_no = 0
        req_case = None
        case_obj = None
        for case in user_cases:
            case_json = case.to_json()
            if "tempmute" not in case_json["action_type"]:
                continue
            temp_cno = int(case_json["case_number"])
            if temp_cno > case_no:
                req_case = case_json
                case_obj = case
                continue
        
        if not req_case:
            return

        start = datetime.fromtimestamp(req_case["created_at"])
        end = datetime.fromtimestamp(req_case["until"])
        end_fmt = end.strftime("%Y-%m-%d %H:%M:%S")
        duration = end - start
        dur_fmt = self.strfdelta(duration)

        data = {
            "case_number": req_case["case_number"],
            "reason": f"{req_case['reason'] if req_case['reason'] else 'No reason provided'}"
                f" | Automatic unmute by {guild.me.mention} after {dur_fmt}.",
            "until": None,
        }

        await case_obj.edit(data=data)
    
    async def check_uslow_expirations(self):
        Member = namedtuple("Member", "id guild")
        while True:
            for guild in self.bot.guilds:
                async with self.config.guild(guild).uslowmodes() as guild_uslowmodes:
                    for channel_id in guild_uslowmodes.copy():
                        for user in guild.members:
                            user_id = user.id
                            async with self.config.member(Member(user_id, guild)).current_slowmodes() as slowmodes:
                                try:
                                    timestamp = slowmodes[channel_id]
                                except:
                                    continue
                                if not timestamp:
                                    continue
                                duration = guild_uslowmodes[channel_id]
                                if not duration:
                                    continue
                                dt_old = datetime.utcfromtimestamp(timestamp)
                                delta = (datetime.utcnow() - dt_old).total_seconds()
                                if delta >= duration:
                                    channel = guild.get_channel(int(channel_id))
                                    await channel.set_permissions(user, overwrite=None)
                                    slowmodes[channel_id] = None
            await asyncio.sleep(120)
                    
    def get_time(self, duration, ret_str=False):
        """Return time variables in appropriate format."""

        reg_ls = ["d[ays]*", "h[ours]*", "m[inutes]*", "s[econds]*"]

        regex = "(?=.+)" + "".join(f"(?:(\\d+)\s*{c})?\s?" for c in reg_ls)
        # can match strings like 
        # 4 days 5 minutes
        # 2days5m
        # 2d 5 min
        # etc

        [(ds, hs, ms, ss)] = re.findall(regex, duration)

        d = int(ds or "0")
        h = int(hs or "0")
        m = int(ms or "0")
        s = int(ss or "0")

        if not ret_str:
            return d, h, m, s

        if ret_str:
            duration_str = "for "
            if d > 1:
                duration_str += f"{d} days "
            elif d > 0:
                duration_str += f"{d} day "

            if h > 1:
                duration_str += f"{h} hours "
            elif h > 0:
                duration_str += f"{h} hour "

            if m > 1:
                duration_str += f"{m} minutes "
            elif m > 0:
                duration_str += f"{m} minute "

            if s > 1:
                duration_str += f"{s} seconds "
            elif s > 0:
                duration_str += f"{s} second "

            return duration_str

    def strfdelta(self, delta):
        s = []
        if delta.days:
            ds = "%i day" % delta.days
            if delta.days > 1:
                ds += "s"
            s.append(ds)
        hrs, rem = divmod(delta.seconds, 60 * 60)
        if hrs:
            hs = "%i hr" % hrs
            if hrs > 1:
                hs += "s"
            s.append(hs)
        mins, secs = divmod(rem, 60)
        if mins:
            s.append("%i min" % mins)
        if secs:
            s.append("%i sec" % secs)
        return " ".join(s)
    
    def utc_timestamp(self, time: datetime) -> float:
        """Return timestamp in UTC.
        
        Parameters
        --------------
        time : datetime
            datetime object in UTC 
        
        Returns
        ---------
        float
            Timestamp in UTC 
        """

        epoch = datetime(1970, 1, 1)
        # get timestamp in UTC 
        timestamp = (time - epoch).total_seconds()

        return timestamp
    
    async def create_temp_unmute_case(self, user: discord.Member, guild: discord.Guild):
        try:
            await modlog.create_case(
                self.bot,
                guild,
                datetime.utcnow(),
                "tempunmute",
                user,
                guild.me,
                reason=f"Automatic unmute by {guild.me.mention}.",
                until=None,
            )
        except RuntimeError as e:
            pass
    
    def cog_unload(self):
        self.tmute_expiry_task.cancel()
        self.uslow_expiry_task.cancel()

    __unload = cog_unload
