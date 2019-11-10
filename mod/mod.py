import asyncio
import logging
import re
from collections import namedtuple
from datetime import datetime, timedelta
from typing import Optional, Union

# Discord
import discord

from redbot.cogs.mod.mod import Mod  # This is the actual mod cog
from redbot.core import Config, checks, commands, modlog
from redbot.core.modlog import Case
from redbot.core.utils.common_filters import filter_invites
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.mod import get_audit_reason, is_allowed_by_hierarchy, is_mod_or_superior
from redbot.core.utils.predicates import ReactionPredicate

log = logging.getLogger("red.extmod")

default_guild = {
    "mute_role_id": None,
    "mute_channel_id": None,
    "current_tempmutes": []
}

default_member = {"muted_until": False}

def _(s): return s

mute_fail = "Failed to mute user. Reason:"
mute_unmute_issues = {
    "already_muted": _("That user can't send messages in this channel."),
    "already_unmuted": _("That user isn't muted in this channel."),
    "hierarchy_problem": _(
        "I cannot let you do that. You are not higher than the user in the role hierarchy."
    ),
    "is_admin": _("That user cannot be muted, as they have the Administrator permission."),
    "permissions_issue": _(
        "Failed to mute user. I need the manage roles "
        "permission and the user I'm muting must be "
        "lower than myself in the role hierarchy."
    ),
}


# This makes sure the cog name is "Mod" for help still.
class NewMod(Mod, name="Mod"):

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
        self.tmute_expiry_task.add_done_callback(error_callback)

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
                "default_setting": False,
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
                "default_setting": False,
                "image": "\N{ALARM CLOCK}\N{SPEAKER}",
                "case_str": "Unmute",
                "audit_type": "overwrite_update"
            },
        ]
        try:
            await modlog.register_casetypes(new_types)
        except RuntimeError:
            pass
    
    # async def cleanup_tasks(self):
    #     await self.bot.wait_until_ready()
    #     while self is self.bot.get_cog("Adventure"):
    #         for task in self.tasks:
    #             if task.done():
    #                 self.tasks.remove(task)
    #         await asyncio.sleep(300)

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @checks.mod_or_permissions(administrator=True)
    async def mute(self, ctx: commands.Context, user: discord.Member, duration: Optional[str], *, reason: str = None):
        """Mute a user.

        If a reason is specified, it will be the reason that shows up
        in the audit log."""

        guild = ctx.guild
        author = ctx.message.author

        is_mod = await is_mod_or_superior(bot=self.bot, obj=user)
        if  is_mod:
            return await ctx.send(f"{mute_fail} user has moderator or superior permissions.")

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
                        return await ctx.send("Insufficient permissions to make a Muted role.")
                else:
                    return await ctx.send(f"{mute_fail} Muted role doesn't exist.")

            mute_role_id = mute_role.id
            # set mute role
            await self.config.guild(guild).mute_role_id.set(mute_role_id)

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
            if re.match("^\d+[d,h,m,s]", duration):
                duration_str = self.get_time(duration, ret_str=True)
                temp = True
            else:
                duration_str = "indefinitely"
                reason = f"{duration} {reason}".strip() if reason else duration
        else:
            duration_str = "indefinitely"

        if temp:
            d, h, m, s = self.get_time(duration)
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

        next_case_no = await modlog.get_next_case_number(guild)

        await mute_channel.send(f"{user.mention} You have been muted {duration_str.strip()}." \
            f" Reason given: {reason}. If you'd like to appeal, send a DM to ModMail or an online moderator.")
        await ctx.send(f"Muted **{user}** {duration_str.strip()}. User notified in {mute_channel.mention}. (Case number {next_case_no-1}) ")
        # await self.update_case_no(ctx)

    @mute.command(name="role")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def set_muted_role(self, ctx, role: discord.Role):
        """Set muted role."""
        role_id = role.id
        await self.config.guild(ctx.guild).mute_role_id.set(role_id)
        await ctx.send(f"Set {role.mention} as the muted role!")

    @mute.command(name="channel")
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def set_muted_channel(self, ctx, channel: discord.TextChannel):
        """Set muted channel."""
        channel_id = channel.id
        await self.config.guild(ctx.guild).mute_channel_id.set(channel_id)
        await ctx.send(f"Set {channel.mention} as the muted channel!")

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

        await ctx.send(f"Unmuted **{user}**.")
    
    @commands.command(aliases=["ui"])
    @commands.bot_has_permissions(embed_links=True)
    @checks.mod_or_permissions(administrator=True)
    async def userinfo(self, ctx, *, user: discord.Member=None):
        """Show information about a user.

        This includes fields for status, discord join date, server
        join date, voice state and previous names/nicknames.

        If the user has no roles, previous names or previous nicknames,
        these fields will be omitted.
        """
        author = ctx.author
        guild = ctx.guild

        if not user:
            user = author

        #  A special case for a special someone :^)
        # special_date = datetime(2016, 1, 10, 6, 8, 4, 443000)
        # is_special = user.id == 96130341705637888 and guild.id == 133049272517001216

        roles = user.roles[-1:0:-1]
        names, nicks = await self.get_names_and_nicks(user)
        cases = await self.cases(ctx, user)

        joined_at = user.joined_at
        since_created = (ctx.message.created_at - user.created_at).days
        if joined_at is not None:
            since_joined = (ctx.message.created_at - joined_at).days
            user_joined = joined_at.strftime("%d %b %Y %H:%M")
        else:
            since_joined = "?"
            user_joined = _("Unknown")
        user_created = user.created_at.strftime("%d %b %Y %H:%M")
        voice_state = user.voice
        member_number = (
            sorted(guild.members, key=lambda m: m.joined_at or ctx.message.created_at).index(user)
            + 1
        )

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
        
        data = discord.Embed(description=activity, colour=user.colour)
        data.add_field(name=_("Joined Discord on"), value=created_on)
        data.add_field(name=_("Joined this server on"), value=joined_on)
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

    @commands.command()
    @commands.guild_only()
    @checks.mod_or_permissions(administrator=True)
    async def note(self, ctx, user: discord.Member, *, note: str = None):
        """Add a note to a user."""

        if not note:
            return await ctx.send("Note is required!")
        
        # try:
        #     await self.config.guild(ctx.guild).notes.set_raw(
        #         user.id, value=note
        #     )
        # except:
        #     return await ctx.send("Error adding note!")
        
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
        
        await ctx.send(f"Add note to **{user}**.")

    async def _mute(self, ctx, user, dur=None):
        """Add/remove mute role. This is a separate function to support temporary mutes."""
        guild = ctx.guild
        
        mute_role_id = await self.config.guild(guild).mute_role_id()
        if mute_role_id in [y.id for y in user.roles]:
            return False, f"{user} is already muted."

        if dur:
            queue_entry = (guild.id, user.id)
            await self.config.member(user).muted_until.set(dur.timestamp())
            cur_tmutes = await self.config.guild(guild).current_tempmutes()
            cur_tmutes.append(user.id)
            await self.config.guild(guild).current_tempmutes.set(cur_tmutes)
        
        mute_role = discord.utils.get(guild.roles, id=mute_role_id)

        await user.add_roles(mute_role)  # adds muted role
        return True, False

    async def cases(self, ctx: commands.Context, user: discord.Member):
        """Get case summary of a member."""
        try:
            user_cases = await modlog.get_cases_for_member(
                bot=self.bot, guild=ctx.guild, member=user
            )
        except discord.NotFound:
            return await ctx.send(_("That user does not exist."))
        except discord.HTTPException:
            return await ctx.send(
                _("Something unexpected went wrong while fetching that user by ID.")
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
    
    async def check_tempmute_expirations(self):
        Member = namedtuple("Member", "id guild")
        while True:
            for guild in self.bot.guilds:
                async with self.config.guild(guild).current_tempmutes() as guild_tempmutes:
                    for uid in guild_tempmutes.copy():
                        unmute_time = datetime.utcfromtimestamp(
                            await self.config.member(Member(uid, guild)).muted_until()
                        )
                        # print(unmute_time)
                        # print(datetime.utcnow())
                        if datetime.utcnow() > unmute_time: # time to unmute the user
                            user = await self.bot.fetch_user(uid)
                            member = discord.utils.get(guild.members, id=uid)
                            queue_entry = (guild.id, user.id)
                            try:
                                mute_role_id = await self.config.guild(guild).mute_role_id()
                                mute_role = discord.utils.get(guild.roles, id=mute_role_id)
                                await member.remove_roles(mute_role)
                                guild_tempmutes.remove(uid)

                                await self.edit_tmute_msg(guild=guild, user=member)

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
    
    # @commands.command()
    # async def get_52(self, ctx: commands.Context, user: discord.Member):
    #     """Get case summary of a member."""
    #     try:
    #         case = await modlog.get_case(case_number=52, guild=ctx.guild, bot=self.bot)
    #     except discord.NotFound:
    #         return await ctx.send(_("That user does not exist."))
    #     except discord.HTTPException:
    #         return await ctx.send(
    #             _("Something unexpected went wrong while fetching that user by ID.")
    #         )

    #     print(case.to_json())
    
    def get_time(self, duration, ret_str=False):
        """Return time variables in appropriate format."""
        regex = "(?=.+)" + "".join(f"(?:(\\d+){c})?" for c in "dhms")

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
    
    def cog_unload(self):
        self.tmute_expiry_task.cancel()

    __unload = cog_unload
