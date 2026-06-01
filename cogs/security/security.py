import json
import re
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands


CONFIG_PATH = Path("config.json")
URL_PATTERN = re.compile(r"https?://[^\s<>()]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>()]*)?", re.IGNORECASE)
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:\w+:\d+>")
INVITE_PATTERN = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[a-z0-9-]+", re.IGNORECASE)
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f]")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def security_config() -> dict[str, Any]:
    config = load_config()
    security = config.get("features", {}).get("security", config.get("security", {}))
    rules = security.get("rules", {})
    if isinstance(rules, dict):
        security = {**security, **rules}
    return security


def get_color(value: str | None, fallback: discord.Color) -> discord.Color:
    if not value:
        return fallback

    try:
        return discord.Color(int(value.strip().lstrip("#"), 16))
    except ValueError:
        return fallback


def normalize_domain(url: str) -> str:
    clean = url.lower().strip("<>()[]{}.,!?\"'")
    clean = re.sub(r"^https?://", "", clean)
    clean = clean.split("/", 1)[0]
    clean = clean.split(":", 1)[0]
    if clean.startswith("www."):
        clean = clean[4:]
    return clean


def domain_matches(domain: str, blocked_domains: list[str]) -> bool:
    for blocked in blocked_domains:
        blocked = blocked.lower().strip()
        if blocked.startswith("www."):
            blocked = blocked[4:]
        if domain == blocked or domain.endswith(f".{blocked}"):
            return True
    return False


def truncate(text: str, limit: int = 900) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def count_unicode_emojis(text: str) -> int:
    count = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x1F300 <= codepoint <= 0x1FAFF
            or 0x2600 <= codepoint <= 0x27BF
            or 0xFE00 <= codepoint <= 0xFE0F
        ):
            count += 1
    return count


class Security(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.message_history: dict[int, deque[tuple[datetime, str]]] = defaultdict(deque)
        self.join_history: deque[datetime] = deque()
        self.raid_mode_until: datetime | None = None

    def is_exempt(self, message: discord.Message, config: dict[str, Any]) -> bool:
        if message.guild is None or not isinstance(message.author, discord.Member):
            return True

        member = message.author
        if member.guild_permissions.administrator and config.get("bypass_admins", True):
            return True

        if message.channel.id in {int(channel_id) for channel_id in config.get("ignored_channel_ids", [])}:
            return True

        if member.id in {int(user_id) for user_id in config.get("ignored_user_ids", [])}:
            return True

        ignored_role_ids = {int(role_id) for role_id in config.get("ignored_role_ids", [])}
        return any(role.id in ignored_role_ids for role in member.roles)

    async def log_action(
        self,
        guild: discord.Guild,
        config: dict[str, Any],
        title: str,
        description: str,
        member: discord.Member | discord.User | None = None,
        message: discord.Message | None = None,
    ) -> None:
        log_channel_id = int(config.get("log_channel_id") or 0)
        if not log_channel_id:
            return

        channel = guild.get_channel(log_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(log_channel_id)
            except discord.DiscordException:
                return

        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=get_color(config.get("log_color"), discord.Color.orange()),
            timestamp=datetime.now(UTC),
        )
        if member is not None:
            embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        if message is not None:
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            if message.content:
                embed.add_field(name="Nachricht", value=truncate(message.content), inline=False)

        try:
            await channel.send(embed=embed)
        except discord.DiscordException:
            pass

    async def punish_message(
        self,
        message: discord.Message,
        config: dict[str, Any],
        rule_name: str,
        reason: str,
        rule_config: dict[str, Any],
    ) -> None:
        if message.guild is None or not isinstance(message.author, discord.Member):
            return

        if rule_config.get("delete_message", True):
            try:
                await message.delete()
            except discord.DiscordException:
                pass

        warn_message = str(rule_config.get("warn_message", "")).strip()
        if warn_message:
            try:
                await message.channel.send(
                    warn_message.format(user_mention=message.author.mention, reason=reason),
                    delete_after=int(rule_config.get("warn_delete_after", 8)),
                )
            except discord.DiscordException:
                pass

        timeout_seconds = int(rule_config.get("timeout_seconds", 0) or 0)
        if timeout_seconds > 0:
            try:
                await message.author.timeout(
                    datetime.now(UTC) + timedelta(seconds=timeout_seconds),
                    reason=f"Security: {rule_name} - {reason}",
                )
            except discord.DiscordException:
                pass

        await self.log_action(
            message.guild,
            config,
            f"Security: {rule_name}",
            reason,
            member=message.author,
            message=message,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        config = security_config()
        if not config.get("enabled", True) or self.is_exempt(message, config):
            return

        content = message.content or ""
        checks = [
            self.check_words_blacklist,
            self.check_links_blacklist,
            self.check_nsfw_links,
            self.check_malicious_links,
            self.check_emoji_spam,
            self.check_walls_of_text,
            self.check_advanced_spam,
        ]

        for check in checks:
            result = check(message, content, config)
            if result is None:
                continue

            rule_name, reason, rule_config = result
            await self.punish_message(message, config, rule_name, reason, rule_config)
            return

    def check_words_blacklist(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("words_blacklist", {})
        if not rule.get("enabled", True):
            return None

        lowered = content.lower()
        for word in rule.get("words", []):
            word = str(word).lower().strip()
            if word and re.search(rf"\b{re.escape(word)}\b", lowered):
                return "Words Blacklist", f"Blockiertes Wort erkannt: `{word}`", rule
        return None

    def check_links_blacklist(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("links_blacklist", {})
        if not rule.get("enabled", True):
            return None

        blocked_domains = [str(domain) for domain in rule.get("domains", [])]
        for url in URL_PATTERN.findall(content):
            domain = normalize_domain(url)
            if domain_matches(domain, blocked_domains):
                return "Links Blacklist", f"Blockierte Domain erkannt: `{domain}`", rule
        return None

    def check_nsfw_links(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("anti_nsfw_links", {})
        if not rule.get("enabled", True):
            return None

        blocked_domains = [str(domain) for domain in rule.get("domains", [])]
        for url in URL_PATTERN.findall(content):
            domain = normalize_domain(url)
            if domain_matches(domain, blocked_domains):
                return "Anti NSFW Links", f"NSFW-Domain erkannt: `{domain}`", rule
        return None

    def check_malicious_links(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("anti_malicious_links", {})
        if not rule.get("enabled", True):
            return None

        blocked_domains = [str(domain) for domain in rule.get("domains", [])]
        for url in URL_PATTERN.findall(content):
            domain = normalize_domain(url)
            if domain_matches(domain, blocked_domains):
                return "Anti Malicious Links", f"Gefährliche Domain erkannt: `{domain}`", rule

        if rule.get("block_discord_invites", True) and INVITE_PATTERN.search(content):
            return "Anti Malicious Links", "Discord-Invite erkannt.", rule

        if rule.get("block_zero_width_obfuscation", True) and ZERO_WIDTH_PATTERN.search(content):
            return "Anti Malicious Links", "Unsichtbare/verschleiernde Zeichen erkannt.", rule

        return None

    def check_emoji_spam(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("anti_emoji_spam", {})
        if not rule.get("enabled", True):
            return None

        custom_count = len(CUSTOM_EMOJI_PATTERN.findall(content))
        emoji_count = custom_count + count_unicode_emojis(content)
        max_emojis = int(rule.get("max_emojis", 12))
        min_ratio = float(rule.get("max_emoji_ratio", 0.65))
        visible_length = max(len(content.strip()), 1)

        if emoji_count > max_emojis:
            return "Anti Emoji Spam", f"Zu viele Emojis: `{emoji_count}`.", rule
        if emoji_count >= 5 and emoji_count / visible_length > min_ratio:
            return "Anti Emoji Spam", f"Emoji-Anteil zu hoch: `{emoji_count}` Emojis.", rule
        return None

    def check_walls_of_text(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("anti_walls_of_text", {})
        if not rule.get("enabled", True):
            return None

        max_chars = int(rule.get("max_characters", 1600))
        max_lines = int(rule.get("max_lines", 18))
        max_repeated = int(rule.get("max_repeated_character", 18))
        max_no_space = int(rule.get("max_characters_without_space", 250))

        if len(content) > max_chars:
            return "Anti Walls Of Text", f"Nachricht ist zu lang: `{len(content)}` Zeichen.", rule
        if content.count("\n") + 1 > max_lines:
            return "Anti Walls Of Text", "Nachricht hat zu viele Zeilen.", rule
        if re.search(rf"(.)\1{{{max_repeated},}}", content):
            return "Anti Walls Of Text", "Zu viele wiederholte Zeichen.", rule
        if re.search(rf"\S{{{max_no_space},}}", content):
            return "Anti Walls Of Text", "Zu langer Text ohne Leerzeichen.", rule
        return None

    def check_advanced_spam(
        self,
        message: discord.Message,
        content: str,
        config: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        rule = config.get("advanced_anti_spam", {})
        if not rule.get("enabled", True):
            return None

        now = datetime.now(UTC)
        window = int(rule.get("window_seconds", 8))
        max_messages = int(rule.get("max_messages", 5))
        max_duplicates = int(rule.get("max_duplicate_messages", 3))
        history = self.message_history[message.author.id]
        history.append((now, content.strip().lower()))

        while history and (now - history[0][0]).total_seconds() > window:
            history.popleft()

        if len(history) > max_messages:
            return "Advanced Anti Spam", f"Zu viele Nachrichten in `{window}` Sekunden.", rule

        if content.strip():
            duplicates = sum(1 for _, old_content in history if old_content == content.strip().lower())
            if duplicates >= max_duplicates:
                return "Advanced Anti Spam", "Zu viele gleiche Nachrichten hintereinander.", rule

        return None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        config = security_config()
        if not config.get("enabled", True):
            return

        rule = config.get("advanced_anti_raid", {})
        if not rule.get("enabled", True):
            return

        now = datetime.now(UTC)
        window = int(rule.get("join_window_seconds", 60))
        self.join_history.append(now)
        while self.join_history and (now - self.join_history[0]).total_seconds() > window:
            self.join_history.popleft()

        threshold = int(rule.get("join_threshold", 8))
        account_age_seconds = int(rule.get("new_account_age_seconds", 86400))
        account_age = (now - member.created_at).total_seconds()
        is_new_account = account_age < account_age_seconds
        raid_detected = len(self.join_history) >= threshold

        if raid_detected:
            self.raid_mode_until = now + timedelta(seconds=int(rule.get("raid_mode_seconds", 300)))
            await self.log_action(
                member.guild,
                config,
                "Security: Advanced Anti Raid",
                f"Raid-Verdacht: `{len(self.join_history)}` Joins in `{window}` Sekunden.",
                member=member,
            )

        raid_mode_active = self.raid_mode_until is not None and self.raid_mode_until > now
        if not raid_mode_active:
            return

        if rule.get("only_punish_new_accounts", True) and not is_new_account:
            return

        action = str(rule.get("raid_action", "timeout")).lower()
        reason = "Security: Advanced Anti Raid"

        if action == "kick":
            try:
                await member.kick(reason=reason)
            except discord.DiscordException:
                pass
            return

        timeout_seconds = int(rule.get("timeout_seconds", 600))
        if timeout_seconds > 0:
            try:
                await member.timeout(now + timedelta(seconds=timeout_seconds), reason=reason)
            except discord.DiscordException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))
