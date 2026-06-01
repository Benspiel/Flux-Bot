import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import commands, tasks


CONFIG_PATH = Path("config.json")
STATE_PATH = Path("data/social_notifications_state.json")
YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_STREAMS_URL = "https://api.twitch.tv/helix/streams"


def load_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return fallback

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def social_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH, {})
    return config.get("social_notifications", {})


def get_color(value: str | None, fallback: discord.Color) -> discord.Color:
    if not value:
        return fallback

    try:
        return discord.Color(int(value.strip().lstrip("#"), 16))
    except ValueError:
        return fallback


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_youtube_channel_id(source: dict[str, Any]) -> str:
    channel_id = str(source.get("channel_id", "")).strip()
    if channel_id:
        return channel_id

    url = str(source.get("url", "")).strip()
    match = re.search(r"youtube\.com/channel/([^/?#]+)", url)
    if match:
        return match.group(1)

    return ""


def find_youtube_channel_id(page_html: str) -> str:
    patterns = [
        r'"channelId":"(UC[^"]+)"',
        r'<meta itemprop="channelId" content="(UC[^"]+)">',
        r'"externalId":"(UC[^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html)
        if match:
            return match.group(1)
    return ""


def parse_twitch_login(source: dict[str, Any]) -> str:
    login = str(source.get("login", source.get("username", ""))).strip().lower()
    if login:
        return login

    url = str(source.get("url", "")).strip().rstrip("/")
    match = re.search(r"twitch\.tv/([^/?#]+)", url)
    if match:
        return match.group(1).lower()

    return ""


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class SocialNotifications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.state = load_json(STATE_PATH, {"youtube": {}, "twitch": {}})
        self.twitch_token: str | None = None
        self.twitch_token_expires_at: datetime | None = None
        self.poll_socials.start()

    def cog_unload(self) -> None:
        self.poll_socials.cancel()
        if self.session is not None:
            self.bot.loop.create_task(self.session.close())

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_text(self, url: str) -> str:
        session = await self.get_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
            response.raise_for_status()
            return await response.text()

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        session = await self.get_session()
        async with session.get(
            url,
            headers=headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def send_embed(
        self,
        channel_id: int,
        embed: discord.Embed,
        content: str = "",
    ) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException as error:
                print(f"Social-Notification-Channel nicht gefunden: {error}")
                return

        if not isinstance(channel, discord.TextChannel):
            print("Social-Notification-Channel ist kein Text-Channel.")
            return

        try:
            await channel.send(content=content, embed=embed)
        except discord.Forbidden:
            print("Mir fehlen Rechte zum Senden im Social-Notification-Channel.")
        except discord.DiscordException as error:
            print(f"Social-Notification konnte nicht gesendet werden: {error}")

    @tasks.loop(seconds=300)
    async def poll_socials(self) -> None:
        config = social_config()
        if not config.get("enabled", True):
            return

        interval = max(int(config.get("poll_interval_seconds", 300)), 60)
        if self.poll_socials.seconds != interval:
            self.poll_socials.change_interval(seconds=interval)

        await self.check_youtube(config.get("youtube", {}))
        await self.check_twitch(config.get("twitch", {}))
        save_json(STATE_PATH, self.state)

    @poll_socials.before_loop
    async def before_poll_socials(self) -> None:
        await self.bot.wait_until_ready()

    async def check_youtube(self, config: dict[str, Any]) -> None:
        if not config.get("enabled", True):
            return

        announce_existing = bool(config.get("announce_existing_on_start", False))
        default_channel_id = int(config.get("discord_channel_id") or config.get("channel_id") or 0)

        for source in config.get("channels", []):
            youtube_channel_id = await self.resolve_youtube_channel_id(source)
            discord_channel_id = int(
                source.get("discord_channel_id") or source.get("channel_id") or default_channel_id
            )
            if not youtube_channel_id or not discord_channel_id:
                continue

            try:
                feed_xml = await self.get_text(YOUTUBE_FEED_URL.format(channel_id=youtube_channel_id))
            except aiohttp.ClientError as error:
                print(f"YouTube-Feed konnte nicht geladen werden ({youtube_channel_id}): {error}")
                continue

            latest_video = self.parse_latest_youtube_video(feed_xml)
            if latest_video is None:
                continue

            source_key = source.get("id") or youtube_channel_id
            source_state = self.state.setdefault("youtube", {}).setdefault(source_key, {})
            last_video_id = source_state.get("last_video_id")
            video_id = latest_video["video_id"]

            if last_video_id == video_id:
                continue

            if last_video_id or announce_existing:
                embed = self.create_youtube_embed(source, latest_video)
                await self.send_embed(
                    discord_channel_id,
                    embed,
                    str(source.get("mention", config.get("mention", ""))).strip(),
                )

            source_state["last_video_id"] = video_id
            source_state["last_checked_at"] = utc_now_iso()

    async def resolve_youtube_channel_id(self, source: dict[str, Any]) -> str:
        channel_id = parse_youtube_channel_id(source)
        if channel_id:
            return channel_id

        url = str(source.get("url", "")).strip()
        if not url:
            return ""

        try:
            page_html = await self.get_text(url)
        except aiohttp.ClientError as error:
            print(f"YouTube-Kanal-Link konnte nicht aufgelöst werden ({url}): {error}")
            return ""

        return find_youtube_channel_id(page_html)

    def parse_latest_youtube_video(self, feed_xml: str) -> dict[str, str] | None:
        namespace = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }
        try:
            root = ET.fromstring(feed_xml)
        except ET.ParseError:
            return None
        entry = root.find("atom:entry", namespace)
        if entry is None:
            return None

        video_id = entry.findtext("yt:videoId", default="", namespaces=namespace)
        title = entry.findtext("atom:title", default="Neues YouTube-Video", namespaces=namespace)
        link = entry.find("atom:link", namespace)
        author = entry.findtext("atom:author/atom:name", default="YouTube", namespaces=namespace)
        published = entry.findtext("atom:published", default="", namespaces=namespace)
        thumbnail = ""

        media_group = entry.find("media:group", namespace)
        if media_group is not None:
            thumbnail_element = media_group.find("media:thumbnail", namespace)
            if thumbnail_element is not None:
                thumbnail = thumbnail_element.attrib.get("url", "")

        if not video_id:
            return None

        return {
            "video_id": video_id,
            "title": title,
            "url": link.attrib.get("href", "") if link is not None else "",
            "author": author,
            "published": published,
            "thumbnail": thumbnail,
        }

    def create_youtube_embed(self, source: dict[str, Any], video: dict[str, str]) -> discord.Embed:
        name = str(source.get("name") or video["author"])
        embed = discord.Embed(
            title=video["title"],
            description=f"**{name}** hat ein neues Video veröffentlicht.",
            url=video["url"],
            color=get_color(source.get("color"), discord.Color.red()),
            timestamp=parse_datetime(video["published"]),
        )
        if video["thumbnail"]:
            embed.set_image(url=video["thumbnail"])
        embed.set_author(name=name)
        embed.set_footer(text="YouTube")
        return embed

    async def get_twitch_token(self) -> str | None:
        client_id = os.getenv("TWITCH_CLIENT_ID")
        client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        if not client_id or not client_secret:
            print("Twitch ist nicht eingerichtet. Prüfe TWITCH_CLIENT_ID und TWITCH_CLIENT_SECRET.")
            return None

        if (
            self.twitch_token
            and self.twitch_token_expires_at
            and self.twitch_token_expires_at > datetime.now(UTC)
        ):
            return self.twitch_token

        session = await self.get_session()
        try:
            async with session.post(
                TWITCH_TOKEN_URL,
                params={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                data = await response.json()
        except aiohttp.ClientError as error:
            print(f"Twitch-Token konnte nicht geladen werden: {error}")
            return None

        self.twitch_token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        self.twitch_token_expires_at = datetime.now(UTC) + timedelta(seconds=max(expires_in - 60, 60))
        return self.twitch_token

    async def check_twitch(self, config: dict[str, Any]) -> None:
        if not config.get("enabled", True):
            return

        sources = config.get("channels", [])
        if not sources:
            return

        token = await self.get_twitch_token()
        client_id = os.getenv("TWITCH_CLIENT_ID")
        if not token or not client_id:
            return

        logins_by_source = {
            str(source.get("id") or parse_twitch_login(source)): parse_twitch_login(source)
            for source in sources
            if parse_twitch_login(source)
        }
        if not logins_by_source:
            return

        params = [("user_login", login) for login in logins_by_source.values()]
        try:
            data = await self.get_json(
                TWITCH_STREAMS_URL,
                headers={
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {token}",
                },
                params=params,
            )
        except aiohttp.ClientError as error:
            print(f"Twitch-Streams konnten nicht geladen werden: {error}")
            return

        live_streams = {stream["user_login"].lower(): stream for stream in data.get("data", [])}
        default_channel_id = int(config.get("discord_channel_id") or config.get("channel_id") or 0)
        announce_existing = bool(config.get("announce_existing_on_start", False))

        for source in sources:
            login = parse_twitch_login(source)
            if not login:
                continue

            source_key = str(source.get("id") or login)
            source_state = self.state.setdefault("twitch", {}).setdefault(source_key, {})
            has_checked_before = "last_checked_at" in source_state
            stream = live_streams.get(login)

            if stream is None:
                source_state["is_live"] = False
                source_state["last_checked_at"] = utc_now_iso()
                continue

            stream_id = stream.get("id")
            already_announced = (
                source_state.get("is_live") is True
                and source_state.get("stream_id") == stream_id
            )
            if already_announced:
                continue

            discord_channel_id = int(
                source.get("discord_channel_id") or source.get("channel_id") or default_channel_id
            )
            if discord_channel_id and (has_checked_before or announce_existing):
                embed = self.create_twitch_embed(source, stream)
                await self.send_embed(
                    discord_channel_id,
                    embed,
                    str(source.get("mention", config.get("mention", ""))).strip(),
                )

            source_state["is_live"] = True
            source_state["stream_id"] = stream_id
            source_state["last_checked_at"] = utc_now_iso()

    def create_twitch_embed(self, source: dict[str, Any], stream: dict[str, Any]) -> discord.Embed:
        login = stream["user_login"]
        display_name = str(source.get("name") or stream.get("user_name") or login)
        stream_url = str(source.get("url") or f"https://www.twitch.tv/{login}")
        thumbnail = str(stream.get("thumbnail_url", "")).format(width=1280, height=720)
        game_name = str(stream.get("game_name") or "Unbekannt")
        viewer_count = int(stream.get("viewer_count") or 0)

        embed = discord.Embed(
            title=stream.get("title", f"{display_name} ist live!"),
            description=f"**{display_name}** ist jetzt live auf Twitch.",
            url=stream_url,
            color=get_color(source.get("color"), discord.Color.purple()),
            timestamp=parse_datetime(stream.get("started_at")),
        )
        embed.add_field(name="Kategorie", value=game_name, inline=True)
        embed.add_field(name="Zuschauer", value=str(viewer_count), inline=True)
        if thumbnail:
            embed.set_image(url=f"{thumbnail}?t={int(datetime.now(UTC).timestamp())}")
        embed.set_author(name=display_name)
        embed.set_footer(text="Twitch")
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SocialNotifications(bot))
