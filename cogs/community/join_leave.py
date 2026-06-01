import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands


CONFIG_PATH = Path("config.json")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def join_leave_config() -> dict[str, Any]:
    config = load_config()
    return config.get("features", {}).get("join_leave", config.get("join_leave", {}))


def get_color(value: str | None, fallback: discord.Color) -> discord.Color:
    if not value:
        return fallback

    try:
        return discord.Color(int(value.strip().lstrip("#"), 16))
    except ValueError:
        return fallback


def format_text(template: str, member: discord.Member) -> str:
    return template.format(
        user=member,
        user_id=member.id,
        user_name=member.name,
        user_mention=member.mention,
        server_name=member.guild.name,
        member_count=member.guild.member_count or 0,
    )


def create_member_embed(
    member: discord.Member,
    embed_config: dict[str, Any],
    fallback_title: str,
    fallback_description: str,
    fallback_color: discord.Color,
) -> discord.Embed:
    embed = discord.Embed(
        title=format_text(embed_config.get("title", fallback_title), member),
        description=format_text(embed_config.get("description", fallback_description), member),
        color=get_color(embed_config.get("color"), fallback_color),
        timestamp=datetime.now(UTC),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    footer = str(embed_config.get("footer", "")).strip()
    if footer:
        embed.set_footer(text=format_text(footer, member))

    return embed


class JoinLeave(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def send_member_embed(
        self,
        member: discord.Member,
        channel_id: int,
        embed: discord.Embed,
        log_name: str,
    ) -> None:
        if not channel_id:
            return

        channel = member.guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException as error:
                print(f"{log_name}-Channel nicht gefunden: {error}")
                return

        if not isinstance(channel, discord.TextChannel):
            print(f"{log_name}-Channel ist kein Text-Channel.")
            return

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"Mir fehlen Rechte zum Senden im {log_name}-Channel.")
        except discord.DiscordException as error:
            print(f"{log_name}-Embed konnte nicht gesendet werden: {error}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        config = join_leave_config()
        if not config.get("enabled", True):
            return

        join_config = config.get("join", {})
        if not join_config.get("enabled", True):
            return

        channel_id = int(join_config.get("channel_id") or config.get("channel_id") or 0)
        embed = create_member_embed(
            member,
            join_config.get("embed", {}),
            "Willkommen!",
            "{user_mention} ist dem Server beigetreten.",
            discord.Color.green(),
        )
        await self.send_member_embed(member, channel_id, embed, "Join")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        config = join_leave_config()
        if not config.get("enabled", True):
            return

        leave_config = config.get("leave", {})
        if not leave_config.get("enabled", True):
            return

        channel_id = int(leave_config.get("channel_id") or config.get("channel_id") or 0)
        embed = create_member_embed(
            member,
            leave_config.get("embed", {}),
            "Auf Wiedersehen!",
            "{user_name} hat den Server verlassen.",
            discord.Color.red(),
        )
        await self.send_member_embed(member, channel_id, embed, "Leave")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JoinLeave(bot))
