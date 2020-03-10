from .automod import AutoMod


async def setup(bot):
    cog = AutoMod(bot)
    bot.add_cog(cog)
