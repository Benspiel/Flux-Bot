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


VERIFY_BUTTON_CUSTOM_ID = "flux_bot:verification:verify"


class VerificationButton(discord.ui.Button):
    def __init__(self, role_id: int, emoji: str) -> None:
        super().__init__(
            label="Verifizieren",
            style=discord.ButtonStyle.success,
            custom_id=VERIFY_BUTTON_CUSTOM_ID,
            emoji=emoji or None,
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "Die Verifizierung ist nur auf einem Server möglich.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message(
                "Die Verification-Rolle wurde nicht gefunden. Bitte kontaktiere das Serverteam.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            try:
                member = await interaction.guild.fetch_member(interaction.user.id)
            except discord.DiscordException:
                await interaction.response.send_message(
                    "Dein Mitgliedsprofil konnte nicht geladen werden. Bitte versuche es erneut.",
                    ephemeral=True,
                )
                return

        if role in member.roles:
            await interaction.response.send_message(
                "Du bist bereits verifiziert.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="Verification-Button")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Mir fehlen Rechte, um dir die Verification-Rolle zu geben.",
                ephemeral=True,
            )
            return
        except discord.DiscordException:
            await interaction.response.send_message(
                "Die Verification-Rolle konnte nicht vergeben werden. Bitte versuche es erneut.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Du wurdest erfolgreich verifiziert.",
            ephemeral=True,
        )


class VerificationView(discord.ui.LayoutView):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(timeout=None)
        embed_config = config.get("embed", {})
        role_id = int(config.get("role_id") or 0)
        emoji = str(config.get("emoji", "✅")).strip()

        components = []

        title = str(embed_config.get("title", "# `✅` Server-Verifizierung")).strip()
        description = str(
            embed_config.get(
                "description",
                "`✅` Willkommen auf dem Discord-Server.\n> Reagiere unten, um dich zu verifizieren.",
            ),
        ).strip()
        if title:
            components.append(discord.ui.TextDisplay(title))

        if title and description:
            components.append(discord.ui.Separator())

        if description:
            components.append(discord.ui.TextDisplay(description))

        for field in embed_config.get("fields", []):
            name = str(field.get("name", "")).strip()
            value = str(field.get("value", "")).strip()
            if name and value:
                components.append(discord.ui.TextDisplay(f"**{name}**\n{value}"))

        image_url = str(embed_config.get("image_url", "")).strip()
        if image_url:
            components.append(discord.ui.Separator())
            components.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(normalize_image_url(image_url)),
                ),
            )

        components.append(discord.ui.Separator())
        components.append(discord.ui.ActionRow(VerificationButton(role_id, emoji)))

        self.add_item(
            discord.ui.Container(
                *components,
                accent_colour=get_color(embed_config.get("color"), discord.Color.green()),
            ),
        )


def create_verification_view(config: dict[str, Any]) -> VerificationView:
    return VerificationView(config)


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

            message = await channel.send(view=create_verification_view(config))
            self.message_id = message.id
            self.panel_sent = True
            print(f"Verification-Panel in #{channel.name} gesendet.")
        except discord.Forbidden:
            print("Mir fehlen Rechte zum Senden/Leeren im Verification-Channel.")
        except discord.DiscordException as error:
            print(f"Verification-Panel konnte nicht gesendet werden: {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Verification(bot))
