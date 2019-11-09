import asyncio
import logging
import re
import datetime
from typing import Optional

# Discord
import discord

# Redbot
from redbot.core import commands, Config
from redbot.cogs.mod.mod import Mod  # This is the actual mod cog
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions

log = logging.getLogger("red.mod")

default_guild = {
    "mute_role": None,
    "mute_channel": None,
    "case_no": 0
}

_ = lambda s: s
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

class Mod(Mod, name="Mod"):  # This makes sure the cog name is "Mod" for help still.

    def __init__(self, bot):
        super().__init__(self, bot)
        self.config = Config.get_conf(
            self, 1_310_127_007, force_registration=True) 
        
        self.tasks = []
        self.cleanup_loop = self.bot.loop.create_task(self.cleanup_tasks())

        self.myconfig.register_guild(**default_guild)

    async def cleanup_tasks(self):
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("Adventure"):
            for task in self.tasks:
                if task.done():
                    self.tasks.remove(task)
            await asyncio.sleep(300)
    
    @commands.command()
    @commands.guild_only()
    @commands.bot_has_permissions(manage_roles=True)
    @checks.mod_or_permissions(administrator=True)
    async def mute(self, ctx: commands.Context, user: discord.Member, duration: Optional[str], *, reason: str = None):
        """"Mute a user.
        
        If a reason is specified, it will be the reason that shows up
        in the audit log."""
        guild = ctx.guild
        author = ctx.message.author
        
        mute_role = await self.config.guild(guild).mute_role()
        
        # check if muted role is set
        if not mute_role:
            mute_role = discord.utils.get(guild.roles, name="Muted") # retrieves muted role if it exists, returns none if there isn't. 
            
            if not mute_role:
                msg = await ctx.send("Muted role not set. Create a new muted role?")
                start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
                pred = ReactionPredicate.yes_or_no(msg, ctx.author)
                await ctx.bot.wait_for("reaction_add", check=pred)
                if pred.result is True:
                    try: # creates muted role 
                        muted = await guild.create_role(name="Muted", reason="To use for muting")
                        for channel in guild.channels: # removes permission to send messages, add reactions and speak in VCs
                            await channel.set_permissions(muted, send_messages=False, add_reactions=False, speak=False)
                        mute_role = muted
                    except discord.Forbidden:
                        return await ctx.send("Insufficient permissions to make a Muted role.")
                else:
                    return await ctx.send(f"{mute_fail} Muted role doesn't exist.")

            # set mute role
            await self.config.guild(guild).mute_role.set(mute_role)

            if re.match("^\d+[d,h,m,s]", duration):
                duration_str = await self.get_time(duration, with_str=True)

            await user.add_roles(mute_role) # adds muted role
            await self._mute(ctx, user)
            prev_case_no = await self.config.guild(guild).case_no() # get previous case number 

            await ctx.send(f"Muted **{user}** for {duration_str.strip()}. (Case number {prev_case_no+1}).")


    async def update_case_no(self, ctx):
        """Update case number."""
        try:
            cur_case_no = await self.config.guild(ctx.guild).case_no()
            await self.config.guild(ctx.guild).case_no.set(cur_case_no+1)
        except Exception as e:
            ctx.send(f"Error *{e}* updating case number.")
    
    async def _mute(self, ctx, user):
        """Add/remove mute role. This is a separate function to support temporary mutes."""
        mute_role = await self.config.guild(ctx.guild).mute_role()

        await user.add_roles(mute_role) # adds muted role

    
    def get_time(self, duration, with_str=None):
        """Return time variables in appropriate format."""
        regex = "(?=.+)" + "".join(f"(?:(\\d+){c})?" for c in "dhms")

        [(ds, hs, ms, ss)] = re.findall(regex, duration)
        d = int(ds or "0")
        h = int(hs or "0")
        m = int(ms or "0")
        s = int(ss or "0")
        
        if not with_str:
            return d, h, m, s

        if with_str:
            duration_str = ""
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

    def cog_unload(self):
        for task in self.tasks:
            log.debug(f"removing task {task}")
            task.cancel()

    __unload = cog_unload
