import json
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands


CONFIG_PATH = Path("config.json")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def counting_config() -> dict[str, Any]:
    config = load_config()
    return config.get("counting", {})


class CountGame(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_number = 0
        self.last_user: int | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        config = counting_config()
        if not config.get("enabled", True):
            return

        channel_id = int(config.get("channel_id") or 0)
        if not channel_id:
            print("Counting nicht eingerichtet. Prüfe counting.channel_id.")
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException as error:
                print(f"Counting-Channel nicht gefunden: {error}")
                return

        if not isinstance(channel, discord.TextChannel):
            print("Counting-Channel ist kein Text-Channel.")
            return

        async for msg in channel.history(limit=200, oldest_first=False):
            if msg.author.bot:
                continue

            if msg.content.strip().isdigit():
                self.last_number = int(msg.content.strip())
                self.last_user = msg.author.id
                print(f"Counting startet in #{channel.name} bei {self.last_number}.")
                return

        print(f"Counting startet in #{channel.name} bei 0.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        config = counting_config()
        if not config.get("enabled", True):
            return

        channel_id = int(config.get("channel_id") or 0)
        if not channel_id or message.channel.id != channel_id:
            return

        content = message.content.strip()
        if not content.isdigit():
            return

        number = int(content)

        if message.author.id == self.last_user:
            await self.user_twice(message)
            return

        if number != self.last_number + 1:
            await self.wrong_number(message)
            return

        self.last_number = number
        self.last_user = message.author.id

        try:
            await message.add_reaction(str(config.get("ok_emoji", "✅")))
        except discord.DiscordException:
            pass

    async def wrong_number(self, message: discord.Message) -> None:
        config = counting_config()

        try:
            await message.add_reaction(str(config.get("fail_emoji", "❌")))
        except discord.DiscordException:
            pass

        await message.channel.send(
            config.get(
                "wrong_number_message",
                "❌ {user_mention} hat die falsche Zahl geschrieben - es geht wieder bei **0** los!",
            ).format(user_mention=message.author.mention)
        )

        self.last_number = 0
        self.last_user = None

    async def user_twice(self, message: discord.Message) -> None:
        config = counting_config()

        try:
            await message.delete()
        except discord.DiscordException:
            pass

        embed = discord.Embed(
            title=config.get("twice_title", "❌ Du darfst nicht zweimal hintereinander zählen!"),
            description=config.get("twice_description", "Bitte warte, bis jemand anderes dran ist."),
            color=discord.Color.red(),
        )
        embed.add_field(name="Deine Nachricht:", value=f"```{message.content}```", inline=False)

        try:
            await message.author.send(embed=embed)
        except discord.DiscordException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CountGame(bot))
