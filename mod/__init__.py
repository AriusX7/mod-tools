from .mod import Mod


async def setup(bot):
    cog = Mod()
    bot.add_cog(cog)
