import asyncio
import json
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
COGS_DIR = BASE_DIR / "cogs"


def load_config() -> dict:
    if not CONFIG_PATH.exists() or CONFIG_PATH.stat().st_size == 0:
        return {}

    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


class FluxBot(commands.Bot):
    def __init__(self) -> None:
        self.config = load_config()
        bot_config = self.config.get("bot", {})

        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix=commands.when_mentioned_or(bot_config.get("command_prefix", "!")),
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        for cog_file in sorted(COGS_DIR.glob("*.py")):
            if cog_file.name.startswith("_"):
                continue

            extension = f"cogs.{cog_file.stem}"
            await self.load_extension(extension)
            logging.info("Loaded extension %s", extension)

        if self.config.get("bot", {}).get("sync_commands_on_start", True):
            synced = await self.tree.sync()
            logging.info("Synced %s application commands", len(synced))

    async def on_ready(self) -> None:
        if self.user is None:
            return

        logging.info("Logged in as %s (%s)", self.user, self.user.id)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(BASE_DIR / ".env")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN fehlt in der .env Datei.")

    async with FluxBot() as bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
