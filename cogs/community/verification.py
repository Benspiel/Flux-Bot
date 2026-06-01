import json
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands


CONFIG_PATH = Path("config.json")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def verification_config() -> dict[str, Any]:
    config = load_config()
    return config.get("verification", {})


def get_color(value: str | None, fallback: discord.Color) -> discord.Color:
    if not value:
        return fallback

    try:
        return discord.Color(int(value.strip().lstrip("#"), 16))
    except ValueError:
        return fallback


def normalize_image_url(image_url: str) -> str:
    image_url = image_url.strip()
    if "github.com/" not in image_url or "/blob/" not in image_url:
        return image_url

    clean_url = image_url.split("?", 1)[0]
    return clean_url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace(
        "/blob/",
        "/",
    )


def create_verification_embed(config: dict[str, Any]) -> discord.Embed:
    embed_config = config.get("embed", {})
    embed = discord.Embed(
        title=embed_config.get("title", "Verifizierung"),
        description=embed_config.get(
            "description",
            "Reagiere mit dem Emoji unten, um dich zu verifizieren.",
        ),
        color=get_color(embed_config.get("color"), discord.Color.green()),
    )

    image_url = str(embed_config.get("image_url", "")).strip()
    if image_url:
        embed.set_image(url=normalize_image_url(image_url))

    footer = str(embed_config.get("footer", "")).strip()
    if footer:
        embed.set_footer(text=footer)

    return embed


class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_sent = False
        self.message_id: int | None = None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.panel_sent:
            return

        config = verification_config()
        if not config.get("enabled", True):
            return

        channel_id = int(config.get("channel_id") or 0)
        role_id = int(config.get("role_id") or 0)
        emoji = str(config.get("emoji", "✅")).strip()

        if not channel_id or not role_id:
            print("Verification nicht eingerichtet. Prüfe verification.channel_id und role_id.")
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException as error:
                print(f"Verification-Channel nicht gefunden: {error}")
                return

        if not isinstance(channel, discord.TextChannel):
            print("Verification-Channel ist kein Text-Channel.")
            return

        try:
            if config.get("clear_channel_on_start", True):
                await channel.purge(limit=None, reason="Verification-Panel beim Start zurückgesetzt")

            message = await channel.send(embed=create_verification_embed(config))
            await message.add_reaction(emoji)
            self.message_id = message.id
            self.panel_sent = True
            print(f"Verification-Panel in #{channel.name} gesendet.")
        except discord.Forbidden:
            print("Mir fehlen Rechte zum Senden/Reagieren/Leeren im Verification-Channel.")
        except discord.DiscordException as error:
            print(f"Verification-Panel konnte nicht gesendet werden: {error}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        config = verification_config()
        if not config.get("enabled", True):
            return

        channel_id = int(config.get("channel_id") or 0)
        role_id = int(config.get("role_id") or 0)
        allowed_emoji = str(config.get("emoji", "✅")).strip()

        if payload.channel_id != channel_id:
            return

        if self.message_id is not None and payload.message_id != self.message_id:
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None:
            return

        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        message = None
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.DiscordException:
            pass

        if str(payload.emoji) != allowed_emoji:
            if message is not None:
                try:
                    await message.remove_reaction(payload.emoji, discord.Object(id=payload.user_id))
                except discord.DiscordException:
                    pass
            return

        role = guild.get_role(role_id)
        if role is None:
            print("Verification-Rolle nicht gefunden. Prüfe verification.role_id.")
            return

        member = payload.member
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.DiscordException:
                return

        try:
            await member.add_roles(role, reason="Verification-Reaktion")
        except discord.Forbidden:
            print("Mir fehlen Rechte, um die Verification-Rolle zu vergeben.")
        except discord.DiscordException as error:
            print(f"Verification-Rolle konnte nicht vergeben werden: {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Verification(bot))
