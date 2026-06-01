import json
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands


CONFIG_PATH = Path("config.json")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def announcement_config() -> dict[str, Any]:
    config = load_config()
    return config.get("features", {}).get("announcements", config.get("announcements", {}))


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


class AnnouncementView(discord.ui.LayoutView):
    def __init__(self, title: str, description: str, config: dict[str, Any]) -> None:
        super().__init__(timeout=None)

        title = title.strip() or "Announcement"
        description = description.strip()
        image_url = str(config.get("image_url", "")).strip()

        components = [
            discord.ui.TextDisplay(f"# {title}"),
        ]

        if description:
            components.append(discord.ui.Separator())
            components.append(discord.ui.TextDisplay(description))

        if image_url:
            components.append(discord.ui.Separator())
            components.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(normalize_image_url(image_url)),
                ),
            )

        self.add_item(
            discord.ui.Container(
                *components,
                accent_colour=get_color(config.get("color"), discord.Color.blurple()),
            ),
        )


async def send_announcement(
    channel: discord.abc.Messageable,
    title: str,
    description: str,
    config: dict[str, Any],
) -> None:
    await channel.send(
        view=AnnouncementView(title, description, config),
        suppress_embeds=True,
    )


class AnnouncementModal(discord.ui.Modal):
    def __init__(
        self,
        cog: "Announcements",
        *,
        title_default: str = "Announcement",
        description_default: str = "",
    ) -> None:
        super().__init__(title="Announcement erstellen")
        self.cog = cog
        self.announcement_title = discord.ui.TextInput(
            label="Titel",
            placeholder="Announcement",
            default=title_default[:120],
            max_length=120,
            required=True,
        )
        self.description = discord.ui.TextInput(
            label="Beschreibung",
            placeholder="Schreibe hier deine Announcement-Nachricht.",
            default=description_default[:3000] or None,
            style=discord.TextStyle.paragraph,
            max_length=3000,
            required=True,
        )
        self.add_item(self.announcement_title)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.send_modal_announcement(
            interaction,
            str(self.announcement_title.value),
            str(self.description.value),
        )


class Announcements(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def get_target_channel(
        self,
        interaction: discord.Interaction,
        config: dict[str, Any],
    ) -> discord.abc.Messageable | None:
        channel_id = int(config.get("channel_id") or 0)
        if not channel_id:
            return interaction.channel

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException:
                return None

        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def send_modal_announcement(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
    ) -> None:
        config = announcement_config()
        if not config.get("enabled", True):
            await interaction.response.send_message(
                "Announcements sind aktuell deaktiviert.",
                ephemeral=True,
            )
            return

        channel = await self.get_target_channel(interaction, config)
        if channel is None:
            await interaction.response.send_message(
                "Der Announcement-Channel wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        try:
            await send_announcement(channel, title, description, config)
        except discord.Forbidden:
            await interaction.response.send_message(
                "Mir fehlen Rechte, um das Announcement zu senden.",
                ephemeral=True,
            )
            return
        except discord.DiscordException:
            await interaction.response.send_message(
                "Das Announcement konnte nicht gesendet werden.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Announcement wurde gesendet.",
            ephemeral=True,
        )

    @app_commands.command(name="announce", description="Erstellt ein Announcement.")
    async def announce(self, interaction: discord.Interaction) -> None:
        config = announcement_config()
        if not config.get("enabled", True):
            await interaction.response.send_message(
                "Announcements sind aktuell deaktiviert.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AnnouncementModal(self))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        config = announcement_config()
        if not config.get("enabled", True) or not config.get("auto_convert_messages", True):
            return

        channel_id = int(config.get("channel_id") or 0)
        if not channel_id or message.channel.id != channel_id:
            return

        content = message.content.strip()
        if not content:
            return

        try:
            await message.delete()
        except discord.Forbidden:
            return
        except discord.DiscordException:
            pass

        try:
            await send_announcement(message.channel, "announce", content, config)
        except discord.DiscordException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Announcements(bot))
