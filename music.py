"""
music.py — Core playback engine (slash commands).

GuildState is keyed by guild_id so multiple guilds stream independently.
_play_next uses state.text_channel instead of ctx so it works without a
command context (called from the audio thread after-callback).
"""

import discord
from discord.ext import commands
from discord import app_commands, ui
from plexapi.server import PlexServer
import asyncio
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class Track:
    title: str
    artist: str
    album: str
    duration_ms: int
    stream_url: str
    thumb_url: Optional[str] = None

    @property
    def duration_str(self) -> str:
        secs = self.duration_ms // 1000
        mins, secs = divmod(secs, 60)
        return f"{mins}:{secs:02d}"

    def __str__(self):
        return f"**{self.title}** — {self.artist}"


@dataclass
class GuildState:
    """Independent playback state per guild — enables simultaneous multi-server streaming."""
    queue: deque = field(default_factory=deque)
    current: Optional[Track] = None
    volume: float = 0.5
    loop: bool = False
    text_channel: Optional[discord.TextChannel] = None  # for _play_next callbacks


# ── Track picker view (/play multi-result) ─────────────────────────────────────

class TrackPickView(ui.View):
    """Shown when /play returns multiple results. Uses a dropdown instead of reactions."""

    def __init__(self, author_id: int, tracks: list, cog):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.tracks = tracks
        self.cog = cog

        options = [
            discord.SelectOption(
                label=t.title[:100],
                description=f"{t.artist} — {t.album}"[:100],
                value=str(i),
            )
            for i, t in enumerate(tracks)
        ]
        select = ui.Select(placeholder="Choose a track to play…", options=options)
        select.callback = self.on_select
        self.add_item(select)

        cancel = ui.Button(label="✕ Cancel", style=discord.ButtonStyle.secondary, row=1)
        cancel.callback = self.on_cancel
        self.add_item(cancel)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🔍 Multiple results found",
            description="Select a track from the dropdown below.",
            color=discord.Color.orange(),
        )
        for i, t in enumerate(self.tracks, 1):
            embed.add_field(
                name=f"{i}. {t.title}",
                value=f"{t.artist}  •  *{t.album}*  •  `{t.duration_str}`",
                inline=False,
            )
        embed.set_footer(text="Expires in 30 seconds.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your search.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        track = self.tracks[int(interaction.data["values"][0])]
        if not interaction.user.voice:
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect()
        elif vc.channel != interaction.user.voice.channel:
            await vc.move_to(interaction.user.voice.channel)

        state = self.cog._state(interaction.guild.id)
        state.text_channel = interaction.channel
        self.stop()

        if vc.is_playing() or vc.is_paused():
            state.queue.append(track)
            embed = discord.Embed(
                description=f"➕ Added **{track.title}** — {track.artist} to the queue.",
                color=discord.Color.orange(),
            )
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            state.current = track
            source = self.cog._make_source(track, state.volume)
            vc.play(source, after=lambda e: self.cog._play_next(state, vc))
            await self.cog._update_presence(track)
            await interaction.response.edit_message(
                embed=self.cog._now_playing_embed(track, state), view=None
            )

    async def on_cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Cancelled.", color=discord.Color.greyple()),
            view=None,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Cog ────────────────────────────────────────────────────────────────────────

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.plex = self._connect_plex()
        self._states: dict[int, GuildState] = {}

    def _connect_plex(self) -> PlexServer:
        base_url = os.getenv("PLEX_URL")
        token = os.getenv("PLEX_TOKEN")
        if not base_url or not token:
            raise EnvironmentError("PLEX_URL and PLEX_TOKEN must be set in .env")
        print(f"Connecting to Plex at {base_url} ...")
        server = PlexServer(base_url, token)
        print(f"Connected to Plex: {server.friendlyName}")
        sections = server.library.sections()
        print(f"Libraries: {[(s.title, s.type) for s in sections]}")
        return server

    def _state(self, guild_id: int) -> GuildState:
        if guild_id not in self._states:
            self._states[guild_id] = GuildState()
        return self._states[guild_id]

    def _music_library(self):
        for s in self.plex.library.sections():
            if s.type == "artist" and s.title.lower() == "music":
                return s
        for s in self.plex.library.sections():
            if s.type == "artist":
                return s
        raise RuntimeError("No Music library found on your Plex server.")

    def _build_track(self, item) -> Track:
        thumb = self.plex.url(item.thumb, includeToken=True) if item.thumb else None
        return Track(
            title=item.title,
            artist=item.grandparentTitle or "Unknown Artist",
            album=item.parentTitle or "Unknown Album",
            duration_ms=item.duration or 0,
            stream_url=item.getStreamURL(),
            thumb_url=thumb,
        )

    def _search_tracks(self, query: str) -> list[Track]:
        lib = self._music_library()
        print(f"Searching '{lib.title}' for: {query}")
        seen, tracks = set(), []

        def add(items, tag):
            for t in items:
                if t.ratingKey not in seen:
                    seen.add(t.ratingKey)
                    tracks.append(self._build_track(t))
                    print(f"  [{tag}] {t.grandparentTitle} - {t.title}")

        r = lib.searchTracks(title=query, maxresults=5)
        print(f"  Track hits: {len(r)}")
        add(r, "track")
        if len(tracks) < 5:
            a = lib.searchArtists(title=query, maxresults=2)
            print(f"  Artist hits: {len(a)}")
            for ar in a:
                add(ar.tracks()[:5], f"artist:{ar.title}")
        if len(tracks) < 5:
            al = lib.searchAlbums(title=query, maxresults=2)
            print(f"  Album hits: {len(al)}")
            for alb in al:
                add(alb.tracks()[:5], f"album:{alb.title}")
        print(f"  Total: {len(tracks)}")
        return tracks[:5]

    def _search_album_tracks(self, query: str) -> list[Track]:
        lib = self._music_library()
        albums = lib.searchAlbums(title=query, maxresults=3)
        return [self._build_track(t) for t in albums[0].tracks()] if albums else []

    def _get_plex_playlist(self, name: str) -> list[Track]:
        for pl in self.plex.playlists():
            if pl.title.lower() == name.lower() and pl.playlistType == "audio":
                return [self._build_track(t) for t in pl.items()]
        return []

    def _make_source(self, track: Track, volume: float) -> discord.PCMVolumeTransformer:
        opts = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "options": "-vn",
        }
        return discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(track.stream_url, **opts), volume=volume)

    async def _set_plex_presence(self):
        """Set bot presence to Plex server stats. Called on startup and periodically."""
        try:
            lib = self._music_library()
            artists = lib.totalViewSize(libtype="artist")
            albums  = lib.totalViewSize(libtype="album")
            tracks  = lib.totalViewSize(libtype="track")
            name = f"{artists:,} artists · {albums:,} albums · {tracks:,} tracks"
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name=name,
            )
            await self.bot.change_presence(status=discord.Status.online, activity=activity)
        except Exception as e:
            print(f"[presence] Could not set Plex presence: {e}")

    def _play_next(self, state: GuildState, vc: discord.VoiceClient):
        """After-callback — no ctx needed, sends to state.text_channel."""
        if state.loop and state.current:
            state.queue.appendleft(state.current)

        if not state.queue:
            state.current = None
            if state.text_channel:
                asyncio.run_coroutine_threadsafe(
                    state.text_channel.send("✅ Queue finished. Disconnecting..."), self.bot.loop
                )
            asyncio.run_coroutine_threadsafe(vc.disconnect(), self.bot.loop)
            return

        next_track = state.queue.popleft()
        state.current = next_track
        source = self._make_source(next_track, state.volume)
        vc.play(source, after=lambda e: self._play_next(state, vc))
        if state.text_channel:
            asyncio.run_coroutine_threadsafe(
                state.text_channel.send(embed=self._now_playing_embed(next_track, state)), self.bot.loop
            )

    def _now_playing_embed(self, track: Track, state: GuildState) -> discord.Embed:
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=str(track),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Album", value=track.album, inline=True)
        embed.add_field(name="Duration", value=track.duration_str, inline=True)
        embed.add_field(name="Volume", value=f"{int(state.volume * 100)}%", inline=True)
        embed.add_field(name="Queue", value=f"{len(state.queue)} track(s) remaining", inline=True)
        if track.thumb_url:
            embed.set_thumbnail(url=track.thumb_url)
        return embed

    async def _connect_voice(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        """Connect to the user's voice channel. Assumes interaction already deferred."""
        if not interaction.user.voice:
            await interaction.followup.send("You must be in a voice channel first.", ephemeral=True)
            return None
        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if vc is None:
            vc = await channel.connect()
        elif vc.channel != channel:
            await vc.move_to(channel)
        return vc

    # ── Slash commands ─────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Search Plex and play a track")
    @app_commands.describe(query="Track name, artist, or album to search for")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        if not interaction.user.voice:
            await interaction.followup.send("You must be in a voice channel first.", ephemeral=True)
            return

        tracks = self._search_tracks(query)
        if not tracks:
            await interaction.followup.send(f"No tracks found for **{query}**.")
            return

        if len(tracks) > 1:
            view = TrackPickView(interaction.user.id, tracks, self)
            await interaction.followup.send(embed=view.build_embed(), view=view)
            return

        track = tracks[0]
        vc = await self._connect_voice(interaction)
        if not vc:
            return
        state = self._state(interaction.guild.id)
        state.text_channel = interaction.channel

        if vc.is_playing() or vc.is_paused():
            state.queue.append(track)
            await interaction.followup.send(f"➕ Added to queue: {track} (`{track.duration_str}`)")
        else:
            state.current = track
            vc.play(self._make_source(track, state.volume), after=lambda e: self._play_next(state, vc))
            await interaction.followup.send(embed=self._now_playing_embed(track, state))

    @app_commands.command(name="playalbum", description="Search Plex and queue a full album")
    @app_commands.describe(query="Album name to search for")
    async def playalbum(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        if not interaction.user.voice:
            await interaction.followup.send("You must be in a voice channel first.", ephemeral=True)
            return

        tracks = self._search_album_tracks(query)
        if not tracks:
            await interaction.followup.send(f"No album found for **{query}**.")
            return

        vc = await self._connect_voice(interaction)
        if not vc:
            return
        state = self._state(interaction.guild.id)
        state.text_channel = interaction.channel

        first, rest = tracks[0], tracks[1:]
        for t in rest:
            state.queue.append(t)

        if vc.is_playing() or vc.is_paused():
            state.queue.appendleft(first)
            await interaction.followup.send(f"💿 Queued **{len(tracks)} tracks** from *{first.album}*.")
        else:
            state.current = first
            vc.play(self._make_source(first, state.volume), after=lambda e: self._play_next(state, vc))
            await interaction.followup.send(
                content=f"💿 Playing **{first.album}** ({len(tracks)} tracks)",
                embed=self._now_playing_embed(first, state),
            )

    @app_commands.command(name="plexlist", description="Play a playlist from your Plex library")
    @app_commands.describe(name="Name of the Plex playlist")
    async def plexlist(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        if not interaction.user.voice:
            await interaction.followup.send("You must be in a voice channel first.", ephemeral=True)
            return

        tracks = self._get_plex_playlist(name)
        if not tracks:
            await interaction.followup.send(f"Plex playlist **{name}** not found or is empty.")
            return

        vc = await self._connect_voice(interaction)
        if not vc:
            return
        state = self._state(interaction.guild.id)
        state.text_channel = interaction.channel

        first, rest = tracks[0], tracks[1:]
        for t in rest:
            state.queue.append(t)

        if vc.is_playing() or vc.is_paused():
            state.queue.appendleft(first)
            await interaction.followup.send(f"📋 Queued **{len(tracks)} tracks** from *{name}*.")
        else:
            state.current = first
            vc.play(self._make_source(first, state.volume), after=lambda e: self._play_next(state, vc))
            await interaction.followup.send(
                content=f"📋 Playing Plex playlist **{name}** ({len(tracks)} tracks)",
                embed=self._now_playing_embed(first, state),
            )

    @app_commands.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Paused.")
        else:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume paused playback")
    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Resumed.")
        else:
            await interaction.response.send_message("Nothing is paused.", ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Skipped.")
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop playback and disconnect from voice")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        state = self._state(interaction.guild.id)
        state.queue.clear()
        state.current = None
        if vc:
            vc.stop()
            await vc.disconnect()
        await self._update_presence(None)
        await interaction.response.send_message("⏹️ Stopped and disconnected.")

    @app_commands.command(name="volume", description="Set playback volume (0–100)")
    @app_commands.describe(level="Volume level from 0 to 100")
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 100]):
        state = self._state(interaction.guild.id)
        state.volume = level / 100
        vc = interaction.guild.voice_client
        if vc and vc.source:
            vc.source.volume = state.volume
        await interaction.response.send_message(f"🔊 Volume set to **{level}%**.")

    @app_commands.command(name="queue", description="Show the current playback queue")
    async def queue_cmd(self, interaction: discord.Interaction):
        state = self._state(interaction.guild.id)
        if not state.current and not state.queue:
            await interaction.response.send_message("📋 The queue is empty.")
            return

        embed = discord.Embed(title="📋 Current Queue", color=discord.Color.orange())
        if state.current:
            embed.add_field(name="▶️ Now Playing", value=f"{state.current} (`{state.current.duration_str}`)", inline=False)
        if state.queue:
            lines = [f"`{i}.` {t} (`{t.duration_str}`)" for i, t in enumerate(list(state.queue)[:10], 1)]
            if len(state.queue) > 10:
                lines.append(f"*...and {len(state.queue) - 10} more*")
            embed.add_field(name="⏭️ Up Next", value="\n".join(lines), inline=False)
        embed.set_footer(text=f"{len(state.queue)} track(s) queued")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show details about the currently playing track")
    async def nowplaying(self, interaction: discord.Interaction):
        state = self._state(interaction.guild.id)
        if not state.current:
            await interaction.response.send_message("Nothing is currently playing.", ephemeral=True)
            return
        await interaction.response.send_message(embed=self._now_playing_embed(state.current, state))

    @app_commands.command(name="clear", description="Clear the playback queue")
    async def clear(self, interaction: discord.Interaction):
        self._state(interaction.guild.id).queue.clear()
        await interaction.response.send_message("🗑️ Queue cleared.")

    @app_commands.command(name="loop", description="Toggle loop mode for the current track")
    async def loop(self, interaction: discord.Interaction):
        state = self._state(interaction.guild.id)
        state.loop = not state.loop
        await interaction.response.send_message(f"Loop {'🔁 enabled' if state.loop else '➡️ disabled'}.")

    @app_commands.command(name="debug", description="Show Plex connection info and test a search")
    @app_commands.describe(query="Optional search term to test against Plex")
    async def debug(self, interaction: discord.Interaction, query: str = None):
        await interaction.response.defer()
        lines = []
        try:
            lines.append(f"**Plex server:** {self.plex.friendlyName}")
            sections = self.plex.library.sections()
            lines.append(f"**Libraries:** {', '.join(f'{s.title} ({s.type})' for s in sections)}")
            music = [s for s in sections if s.type == "artist"]
            if not music:
                lines.append("⚠️ No artist-type library found!")
            else:
                lib = music[0]
                lines.append(f"**Music library:** {lib.title} — {lib.totalSize} items")
                if query:
                    t = lib.searchTracks(title=query, maxresults=3)
                    a = lib.searchArtists(title=query, maxresults=3)
                    al = lib.searchAlbums(title=query, maxresults=3)
                    lines += [
                        f"**Raw search for '{query}':**",
                        f"Tracks ({len(t)}): {[x.title for x in t]}",
                        f"Artists ({len(a)}): {[x.title for x in a]}",
                        f"Albums ({len(al)}): {[x.title for x in al]}",
                    ]
        except Exception as e:
            lines.append(f"❌ Error: {e}")
        await interaction.followup.send("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
