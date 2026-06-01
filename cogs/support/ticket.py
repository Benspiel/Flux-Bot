import json
import re
from pathlib import Path
from random import randint
from typing import Any

import discord
from discord.ext import commands


CONFIG_PATH = Path("config.json")
TICKET_SELECT_ID = "ticket_pool:create"
TICKET_CLOSE_ID = "ticket_pool:close"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def ticket_config() -> dict[str, Any]:
    config = load_config()
    return config.get("ticket_system", config)


def get_color(value: str | None, fallback: discord.Color) -> discord.Color:
    if not value:
        return fallback

    try:
        return discord.Color(int(value.strip().lstrip("#"), 16))
    except ValueError:
        return fallback


def get_ticket_options() -> list[dict[str, Any]]:
    config = ticket_config()
    return config.get("ticket_options", config.get("categories", []))


def normalize_image_url(image_url: str) -> str:
    image_url = image_url.strip()
    if "github.com/" not in image_url or "/blob/" not in image_url:
        return image_url

    clean_url = image_url.split("?", 1)[0]
    return clean_url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace(
        "/blob/",
        "/",
    )


def get_banner_file() -> discord.File | None:
    banner = ticket_config().get("banner", {})
    file_path = str(banner.get("local_file", "")).strip()
    if not file_path:
        return None

    path = Path(file_path)
    if not path.exists():
        return None

    return discord.File(path, filename=path.name)


def get_banner_image_url() -> str | None:
    config = ticket_config()
    image_url = str(config.get("image_url", "")).strip()
    if image_url:
        return normalize_image_url(image_url)

    banner = config.get("banner", {})
    image_url = str(banner.get("image_url", "")).strip()
    if image_url:
        return normalize_image_url(image_url)

    file_path = str(banner.get("local_file", "")).strip()
    if file_path and Path(file_path).exists():
        return f"attachment://{Path(file_path).name}"

    return None


def create_ticket_topic(user: discord.abc.User, category: str, ticket_id: int) -> str:
    return f"ticket_owner_id={user.id};ticket_category={category};ticket_id={ticket_id}"


def parse_ticket_topic(topic: str | None) -> dict[str, str]:
    if not topic:
        return {}

    values = {}
    for part in topic.split(";"):
        key, separator, value = part.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def shorten_text(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def find_option_config(config: dict[str, Any], label: str) -> dict[str, Any]:
    for option in config.get("ticket_options", config.get("categories", [])):
        if option.get("label") == label:
            return option
    return {}


def get_question_for_category(config: dict[str, Any], category: str) -> str:
    option = find_option_config(config, category)
    return str(option.get("question", "")).strip()


def create_ticket_embed(
    user: discord.abc.User,
    category: str,
    ticket_id: int,
    reason: str | None = None,
) -> discord.Embed:
    config = ticket_config()
    messages = config.get("messages", {})
    ticket_embed = config.get("ticket_embed", {})
    reason_text = reason.strip() if reason else messages.get("no_reason", "Nicht angegeben")

    description = ticket_embed.get(
        "description",
        "Hallo {user_mention}! Ein Teammitglied hilft dir gleich weiter.",
    ).format(user_mention=user.mention)

    embed = discord.Embed(
        title=ticket_embed.get("title", "Support Ticket"),
        description=description,
        color=get_color(ticket_embed.get("color"), discord.Color.green()),
    )
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
    embed.add_field(name="Ticket ID", value=str(ticket_id), inline=True)
    embed.add_field(name="Kategorie", value=category, inline=True)
    embed.add_field(name="Ersteller", value=user.mention, inline=True)
    embed.add_field(name="Grund", value=shorten_text(reason_text, 1024), inline=False)

    image_url = get_banner_image_url()
    if image_url:
        embed.set_image(url=image_url)

    footer = str(ticket_embed.get("footer", "")).strip()
    if footer:
        embed.set_footer(text=footer.format(ticket_id=ticket_id))

    return embed


def get_support_role_ids() -> set[int]:
    config = ticket_config()
    return {
        int(role_id)
        for role_id in config.get(
            "support_role_ids",
            config.get("permissions", {}).get("support_role_ids", []),
        )
        if str(role_id).isdigit()
    }


def user_can_close_ticket(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False

    if member.guild_permissions.manage_channels:
        return True

    support_role_ids = get_support_role_ids()
    return any(role.id in support_role_ids for role in member.roles)


def find_existing_ticket(
    guild: discord.Guild,
    user: discord.abc.User,
    category: str,
) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        ticket_data = parse_ticket_topic(channel.topic)
        if (
            ticket_data.get("ticket_owner_id") == str(user.id)
            and ticket_data.get("ticket_category") == category
        ):
            return channel
    return None


def create_channel_name(prefix: str, selected: str, user: discord.abc.User) -> str:
    raw_name = f"{prefix}-{selected}-{user.name}".lower()
    clean_name = re.sub(r"[^a-z0-9-]", "-", raw_name)
    clean_name = re.sub(r"-+", "-", clean_name).strip("-")
    return clean_name[:90] or "ticket"


async def create_ticket(
    interaction: discord.Interaction,
    selected: str,
    reason: str | None = None,
) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    config = ticket_config()
    channel_config = config.get("channels", {})
    messages = config.get("messages", {})
    guild = interaction.guild
    user = interaction.user

    if guild is None:
        await interaction.followup.send(
            messages.get("guild_only", "Tickets können nur auf einem Server erstellt werden."),
            ephemeral=True,
        )
        return

    existing_ticket = find_existing_ticket(guild, user, selected)
    if existing_ticket:
        await interaction.followup.send(
            messages.get(
                "already_open",
                "Du hast bereits ein offenes Ticket: {channel_mention}",
            ).format(channel_mention=existing_ticket.mention),
            ephemeral=True,
        )
        return

    category_id = int(config.get("ticket_category_id", channel_config.get("category_id") or 0))
    category = guild.get_channel(category_id) if category_id else None
    ticket_id = randint(1000, 9999)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
    }

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        )

    for role_id in get_support_role_ids():
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            )

    channel_name = create_channel_name(
        config.get("ticket_prefix", channel_config.get("ticket_prefix", "ticket")),
        selected,
        user,
    )

    try:
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=create_ticket_topic(user, selected, ticket_id),
            overwrites=overwrites,
            reason=f"Ticket erstellt von {user} - {selected}",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            messages.get(
                "missing_channel_permission",
                "Ich habe keine Berechtigung, Ticket-Channels zu erstellen.",
            ),
            ephemeral=True,
        )
        return

    support_mentions = " ".join(
        role.mention for role_id in get_support_role_ids() if (role := guild.get_role(role_id))
    )
    await ticket_channel.send(
        messages.get(
            "ticket_created_message",
            "{user_mention} hat ein neues Ticket erstellt.",
        ).format(user_mention=user.mention, support_mentions=support_mentions).strip(),
        allowed_mentions=discord.AllowedMentions(users=True, roles=True),
    )

    embed = create_ticket_embed(user, selected, ticket_id, reason)
    file = get_banner_file()
    if file:
        await ticket_channel.send(embed=embed, file=file, view=TicketCloseView())
    else:
        await ticket_channel.send(embed=embed, view=TicketCloseView())

    await interaction.followup.send(
        messages.get(
            "created",
            "Dein Ticket wurde erstellt: {channel_mention}",
        ).format(channel_mention=ticket_channel.mention),
        ephemeral=True,
    )


class TicketQuestionModal(discord.ui.Modal):
    def __init__(self, category: str, question: str):
        super().__init__(title=shorten_text(f"Ticket: {category}", 45))
        self.category = category
        self.answer = discord.ui.TextInput(
            label=shorten_text(question, 45),
            placeholder=shorten_text(question, 100),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await create_ticket(interaction, self.category, str(self.answer.value))


class TicketDropdown(discord.ui.Select):
    def __init__(self, options_config: list[dict[str, Any]]):
        options = [
            discord.SelectOption(
                label=option["label"],
                description=option.get("description", ""),
                emoji=option.get("emoji"),
            )
            for option in options_config
        ]
        config = ticket_config()
        placeholder = config.get(
            "placeholder",
            config.get("panel", {}).get("placeholder", "Kategorie auswählen..."),
        )
        super().__init__(
            custom_id=TICKET_SELECT_ID,
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        config = ticket_config()
        messages = config.get("messages", {})
        guild = interaction.guild
        selected = self.values[0]

        if guild is None:
            await interaction.response.send_message(
                messages.get("guild_only", "Tickets können nur auf einem Server erstellt werden."),
                ephemeral=True,
            )
            return

        existing_ticket = find_existing_ticket(guild, interaction.user, selected)
        if existing_ticket:
            await interaction.response.send_message(
                messages.get(
                    "already_open",
                    "Du hast bereits ein offenes Ticket: {channel_mention}",
                ).format(channel_mention=existing_ticket.mention),
                ephemeral=True,
            )
            return

        question = get_question_for_category(config, selected)
        if question:
            await interaction.response.send_modal(TicketQuestionModal(selected, question))
            return

        await create_ticket(interaction, selected)


class TicketView(discord.ui.View):
    def __init__(self, options_config: list[dict[str, Any]]):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown(options_config))


class TicketPanelView(discord.ui.LayoutView):
    def __init__(self, options_config: list[dict[str, Any]]):
        super().__init__(timeout=None)
        config = ticket_config()
        panel = config.get("panel", {})
        title = str(panel.get("title", "# `🎫` Support Tickets")).strip()
        description = str(
            panel.get(
                "description",
                "`📌` Wähle unten eine Kategorie aus, um ein neues Ticket zu öffnen.",
            ),
        ).strip()

        components = []

        if title:
            components.append(discord.ui.TextDisplay(title))

        if title and description:
            components.append(discord.ui.Separator())

        if description:
            components.append(discord.ui.TextDisplay(description))

        image_url = get_banner_image_url()
        if image_url:
            components.append(discord.ui.Separator())
            components.append(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(image_url),
                ),
            )

        components.append(discord.ui.Separator())
        components.append(discord.ui.ActionRow(TicketDropdown(options_config)))

        self.add_item(
            discord.ui.Container(
                *components,
                accent_colour=get_color(panel.get("color"), discord.Color.blurple()),
            ),
        )


class TicketCloseConfirmView(discord.ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True

        await interaction.response.send_message(
            ticket_config()
            .get("messages", {})
            .get(
                "close_confirm_owner_only",
                "Nur die Person, die das Schließen gestartet hat, kann hier bestätigen.",
            ),
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Bestätigen", style=discord.ButtonStyle.danger)
    async def confirm_close(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        messages = ticket_config().get("messages", {})
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.edit_message(
                content=messages.get(
                    "not_a_ticket_channel",
                    "Dieses Ticket kann hier nicht geschlossen werden.",
                ),
                view=None,
            )
            return

        if not user_can_close_ticket(interaction):
            await interaction.response.edit_message(
                content=messages.get("close_denied", "Du darfst dieses Ticket nicht schließen."),
                view=None,
            )
            return

        channel = interaction.channel
        await interaction.response.edit_message(
            content=messages.get("closing", "Ticket wird geschlossen..."),
            view=None,
        )
        await channel.delete(reason=f"Ticket geschlossen von {interaction.user}")

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel_close(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content=ticket_config()
            .get("messages", {})
            .get("close_cancelled", "Schließen abgebrochen."),
            view=None,
        )


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Schließen",
        style=discord.ButtonStyle.danger,
        custom_id=TICKET_CLOSE_ID,
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        messages = ticket_config().get("messages", {})
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                messages.get(
                    "not_a_ticket_channel",
                    "Dieses Ticket kann hier nicht geschlossen werden.",
                ),
                ephemeral=True,
            )
            return

        if not user_can_close_ticket(interaction):
            await interaction.response.send_message(
                messages.get("close_denied", "Du darfst dieses Ticket nicht schließen."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            messages.get("close_question", "Möchtest du dieses Ticket wirklich schließen?"),
            view=TicketCloseConfirmView(interaction.user.id),
            ephemeral=True,
        )


async def send_ticket_panel(channel: discord.TextChannel, options_config: list[dict[str, Any]]):
    await channel.send(view=TicketPanelView(options_config))


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_sent = False
        if not ticket_config().get("enabled", True):
            return

        options = get_ticket_options()
        if options:
            self.bot.add_view(TicketPanelView(options))
        self.bot.add_view(TicketCloseView())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.panel_sent:
            return

        config = ticket_config()
        if not config.get("enabled", True):
            return

        panel = config.get("panel", {})
        channel_id = int(config.get("ticket_channel_id", panel.get("channel_id") or 0))
        channel = self.bot.get_channel(channel_id) if channel_id else None

        if channel is None and channel_id:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException as error:
                print(f"Ticket-Channel nicht gefunden: {error}")
                return

        if not isinstance(channel, discord.TextChannel):
            print("Ticket-Panel-Channel nicht gefunden. Prüfe ticket_system.panel.channel_id.")
            return

        try:
            if panel.get("clear_channel_on_start", True):
                await channel.purge(limit=None, reason="Ticket-Panel beim Start zurückgesetzt")
            await send_ticket_panel(channel, get_ticket_options())
            self.panel_sent = True
            print(f"Ticket-Panel in #{channel.name} gesendet.")
        except discord.Forbidden:
            print("Mir fehlen Rechte zum Leeren/Senden im Ticket-Panel-Channel.")
        except discord.DiscordException as error:
            print(f"Ticket-Panel konnte nicht gesendet werden: {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
