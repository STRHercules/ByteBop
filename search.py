"""
search.py — /search slash command with paginated results and dropdown actions.
"""

import discord
from discord.ext import commands
from discord import app_commands, ui
from plexapi.server import PlexServer
import asyncio
from typing import Optional

from music import GuildState, Track
from playlists import _list_playlists, _load_playlist, _save_playlist, _track_to_dict

PAGE_SIZE = 10


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_track(plex: PlexServer, item) -> tuple[Track, int]:
    thumb = plex.url(item.thumb, includeToken=True) if item.thumb else None
    return Track(
        title=item.title,
        artist=item.grandparentTitle or "Unknown Artist",
        album=item.parentTitle or "Unknown Album",
        duration_ms=item.duration or 0,
        stream_url=item.getStreamURL(),
        thumb_url=thumb,
    ), item.ratingKey


def _track_detail_embed(track: Track) -> discord.Embed:
    embed = discord.Embed(description=f"**{track.title}**", color=discord.Color.orange())
    embed.add_field(name="Artist", value=track.artist, inline=True)
    embed.add_field(name="Album", value=track.album, inline=True)
    embed.add_field(name="Duration", value=track.duration_str, inline=True)
    if track.thumb_url:
        embed.set_thumbnail(url=track.thumb_url)
    embed.set_footer(text="Choose an action below.")
    return embed


# ── Playlist select view ───────────────────────────────────────────────────────

class PlaylistSelectView(ui.View):
    def __init__(self, author_id: int, track: Track, rating_key: int, guild_id: int, back_view):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.back_view = back_view

        playlists = _list_playlists(guild_id)
        options = [discord.SelectOption(label=pl["name"][:100], value=pl["name"][:100]) for pl in playlists[:25]]
        if not options:
            return

        async def on_select(interaction: discord.Interaction):
            pl_name = interaction.data["values"][0]
            data = _load_playlist(guild_id, pl_name)
            if data is None:
                await interaction.response.send_message(f"Playlist **{pl_name}** not found.", ephemeral=True)
                return
            data["tracks"].append(_track_to_dict(track, rating_key))
            _save_playlist(guild_id, data)
            embed = discord.Embed(
                description=f"✅ Added **{track.title}** — {track.artist} to **{pl_name}** ({len(data['tracks'])} tracks).",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=embed, view=back_view)

        select = ui.Select(placeholder="Choose a playlist…", options=options)
        select.callback = on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your search.", ephemeral=True)
            return False
        return True

    @ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=1)
    async def go_back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=_track_detail_embed(self.back_view.track), view=self.back_view)


# ── Track action view ──────────────────────────────────────────────────────────

class TrackActionView(ui.View):
    def __init__(self, author_id: int, track: Track, rating_key: int, cog, search_view):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.track = track
        self.rating_key = rating_key
        self.cog = cog  # Search cog
        self.search_view = search_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your search.", ephemeral=True)
            return False
        return True

    @ui.button(label="▶ Play", style=discord.ButtonStyle.success, row=0)
    async def play(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.voice:
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect()
        elif vc.channel != interaction.user.voice.channel:
            await vc.move_to(interaction.user.voice.channel)

        music = self.cog.music_cog
        state = music._state(interaction.guild.id)
        state.text_channel = interaction.channel
        self.stop()

        if vc.is_playing() or vc.is_paused():
            state.queue.append(self.track)
            embed = discord.Embed(
                description=f"➕ Added **{self.track.title}** — {self.track.artist} to the queue.",
                color=discord.Color.orange(),
            )
        else:
            state.current = self.track
            vc.play(music._make_source(self.track, state.volume), after=lambda e: music._play_next(state, vc))
            await music._update_presence(self.track)
            embed = music._now_playing_embed(self.track, state)

        await interaction.response.edit_message(embed=embed, view=None)

    @ui.button(label="📋 Add to Playlist", style=discord.ButtonStyle.primary, row=0)
    async def add_to_playlist(self, interaction: discord.Interaction, button: ui.Button):
        playlists = _list_playlists(interaction.guild.id)
        if not playlists:
            await interaction.response.send_message(
                "No playlists yet. Create one with `/playlist create`.", ephemeral=True
            )
            return
        view = PlaylistSelectView(self.author_id, self.track, self.rating_key, interaction.guild.id, back_view=self)
        embed = discord.Embed(
            title="📋 Add to Playlist",
            description=f"Select a playlist to add **{self.track.title}** to:",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @ui.button(label="🎤 Search Artist", style=discord.ButtonStyle.secondary, row=0)
    async def search_artist(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        results = self.cog._search_by_artist(self.track.artist)
        if not results:
            await interaction.followup.send(f"No results for artist **{self.track.artist}**.", ephemeral=True)
            return
        view = SearchView(self.author_id, self.track.artist, results, self.cog, label=f"Top tracks by {self.track.artist}")
        await interaction.edit_original_response(embed=view.build_embed(), view=view)

    @ui.button(label="💿 Search Album", style=discord.ButtonStyle.secondary, row=0)
    async def search_album(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        results = self.cog._search_by_album(self.track.artist, self.track.album)
        if not results:
            await interaction.followup.send(f"No results for album **{self.track.album}**.", ephemeral=True)
            return
        view = SearchView(self.author_id, self.track.album, results, self.cog, label=f"Tracks in {self.track.album}")
        await interaction.edit_original_response(embed=view.build_embed(), view=view)

    @ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=1)
    async def go_back(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(embed=self.search_view.build_embed(), view=self.search_view)


# ── Paginated search view ──────────────────────────────────────────────────────

class SearchView(ui.View):
    def __init__(self, author_id: int, query: str, results: list,
                 cog, page: int = 0, label: str = None):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.query = query
        self.results = results
        self.cog = cog
        self.page = page
        self.label = label
        self.total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        start = self.page * PAGE_SIZE
        page_results = self.results[start: start + PAGE_SIZE]

        options = [
            discord.SelectOption(
                label=f"{start + i + 1}. {t.title}"[:100],
                description=f"{t.artist} — {t.album}"[:100],
                value=str(i),
            )
            for i, (t, _) in enumerate(page_results)
        ]
        select = ui.Select(placeholder="Select a track for actions…", options=options, row=0)
        select.callback = self._on_select
        self.add_item(select)

        prev = ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=(self.page == 0), row=1)
        prev.callback = self._prev
        self.add_item(prev)

        nxt = ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1), row=1)
        nxt.callback = self._next
        self.add_item(nxt)

        close = ui.Button(label="✕ Close", style=discord.ButtonStyle.danger, row=1)
        close.callback = self._close
        self.add_item(close)

    def build_embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        title = self.label or f"🔍 Results for \"{self.query}\""
        embed = discord.Embed(
            title=title,
            description=f"{len(self.results)} result(s)  •  Page {self.page + 1} of {self.total_pages}",
            color=discord.Color.orange(),
        )
        for i, (t, _) in enumerate(self.results[start: start + PAGE_SIZE], start=1):
            embed.add_field(
                name=f"{start + i}. {t.title}",
                value=f"{t.artist}  •  *{t.album}*  •  `{t.duration_str}`",
                inline=False,
            )
        embed.set_footer(text="Select a track from the dropdown for actions.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your search.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        idx = int(interaction.data["values"][0])
        track, rating_key = self.results[self.page * PAGE_SIZE + idx]
        view = TrackActionView(self.author_id, track, rating_key, self.cog, search_view=self)
        await interaction.response.edit_message(embed=_track_detail_embed(track), view=view)

    async def _prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _close(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="Search closed.", color=discord.Color.greyple()), view=None
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Cog ────────────────────────────────────────────────────────────────────────

class Search(commands.Cog):
    def __init__(self, bot: commands.Bot, music_cog):
        self.bot = bot
        self.music_cog = music_cog
        self.plex = music_cog.plex

    def _broad_search(self, query: str, max_results: int = 50) -> list[tuple[Track, int]]:
        lib = self.music_cog._music_library()
        print(f"[search] broad: {query}")
        seen, results = set(), []

        def add(items):
            for t in items:
                if t.ratingKey not in seen and len(results) < max_results:
                    seen.add(t.ratingKey)
                    results.append(_make_track(self.plex, t))

        add(lib.searchTracks(title=query, maxresults=max_results))
        if len(results) < max_results:
            for ar in lib.searchArtists(title=query, maxresults=5):
                add(ar.tracks()[:10])
        if len(results) < max_results:
            for al in lib.searchAlbums(title=query, maxresults=5):
                add(al.tracks())

        print(f"[search] found {len(results)}")
        return results

    def _search_by_artist(self, artist_name: str) -> list[tuple[Track, int]]:
        lib = self.music_cog._music_library()
        seen, results = set(), []
        for ar in lib.searchArtists(title=artist_name, maxresults=3):
            for t in ar.tracks():
                if t.ratingKey not in seen:
                    seen.add(t.ratingKey)
                    results.append(_make_track(self.plex, t))
        return results[:50]

    def _search_by_album(self, artist_name: str, album_name: str) -> list[tuple[Track, int]]:
        lib = self.music_cog._music_library()
        albums = lib.searchAlbums(title=album_name, maxresults=5)
        for al in albums:
            if al.parentTitle.lower() == artist_name.lower():
                return [_make_track(self.plex, t) for t in al.tracks()]
        return [_make_track(self.plex, t) for t in albums[0].tracks()] if albums else []

    @app_commands.command(name="search", description="Search your Plex library with paginated results")
    @app_commands.describe(query="Artist, track, or album name to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        results = self._broad_search(query)
        if not results:
            await interaction.followup.send(f"No results found for **{query}**.")
            return
        view = SearchView(interaction.user.id, query, results, self)
        await interaction.followup.send(embed=view.build_embed(), view=view)


async def setup(bot: commands.Bot):
    music_cog = bot.cogs.get("Music")
    if music_cog is None:
        raise RuntimeError("Search cog requires Music cog to be loaded first.")
    await bot.add_cog(Search(bot, music_cog))
