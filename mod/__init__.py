from .mod import ExtMod


async def setup(bot):
    cog = ExtMod(bot)
    await cog.initialize()
    bot.add_cog(cog)
