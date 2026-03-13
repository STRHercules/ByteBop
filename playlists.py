"""
playlists.py — Custom playlist management (slash command group).
Playlists saved as JSON in ./playlists/<guild_id>/<name>.json
"""

import discord
from discord.ext import commands
from discord import app_commands, ui
from plexapi.server import PlexServer
import asyncio
import os
import json
from datetime import datetime
from typing import Optional

from music import GuildState, Track

PLAYLISTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playlists")


# ── File helpers ───────────────────────────────────────────────────────────────

def _guild_dir(guild_id: int) -> str:
    path = os.path.join(PLAYLISTS_DIR, str(guild_id))
    os.makedirs(path, exist_ok=True)
    return path

def _playlist_path(guild_id: int, name: str) -> str:
    return os.path.join(_guild_dir(guild_id), f"{name.lower().replace(' ', '_')}.json")

def _load_playlist(guild_id: int, name: str) -> Optional[dict]:
    path = _playlist_path(guild_id, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_playlist(guild_id: int, data: dict):
    with open(_playlist_path(guild_id, data["name"]), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _list_playlists(guild_id: int) -> list[dict]:
    d = _guild_dir(guild_id)
    result = []
    for fname in sorted(os.listdir(d)):
        if fname.endswith(".json"):
            with open(os.path.join(d, fname), "r", encoding="utf-8") as f:
                result.append(json.load(f))
    return result

def _delete_playlist(guild_id: int, name: str) -> bool:
    path = _playlist_path(guild_id, name)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False

def _track_to_dict(track: Track, rating_key: int = None) -> dict:
    return {
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration_ms": track.duration_ms,
        "plex_rating_key": rating_key,
    }

def _dict_to_track(plex: PlexServer, entry: dict) -> Optional[Track]:
    try:
        item = plex.fetchItem(entry["plex_rating_key"])
        thumb = plex.url(item.thumb, includeToken=True) if item.thumb else None
        return Track(
            title=entry["title"], artist=entry["artist"], album=entry["album"],
            duration_ms=entry["duration_ms"], stream_url=item.getStreamURL(), thumb_url=thumb,
        )
    except Exception as e:
        print(f"  [playlist] Could not resolve '{entry['title']}': {e}")
        return None


# ── Autocomplete ───────────────────────────────────────────────────────────────

async def playlist_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    playlists = _list_playlists(interaction.guild.id)
    return [
        app_commands.Choice(name=pl["name"], value=pl["name"])
        for pl in playlists if current.lower() in pl["name"].lower()
    ][:25]


# ── Cog ────────────────────────────────────────────────────────────────────────

class Playlists(commands.Cog):
    def __init__(self, bot: commands.Bot, music_cog):
        self.bot = bot
        self.music_cog = music_cog
        self.plex = music_cog.plex
        self._states = music_cog._states

    def _state(self, guild_id: int) -> GuildState:
        return self.music_cog._state(guild_id)

    async def _connect_voice(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        if not interaction.user.voice:
            await interaction.followup.send("You must be in a voice channel first.", ephemeral=True)
            return None
        vc = interaction.guild.voice_client
        if vc is None:
            vc = await interaction.user.voice.channel.connect()
        elif vc.channel != interaction.user.voice.channel:
            await vc.move_to(interaction.user.voice.channel)
        return vc

    def _search_with_keys(self, query: str) -> list[tuple[Track, int]]:
        lib = self.music_cog._music_library()
        seen, results = set(), []

        def add(items):
            for t in items:
                if t.ratingKey not in seen and len(results) < 5:
                    seen.add(t.ratingKey)
                    thumb = self.plex.url(t.thumb, includeToken=True) if t.thumb else None
                    track = Track(
                        title=t.title, artist=t.grandparentTitle or "Unknown",
                        album=t.parentTitle or "Unknown", duration_ms=t.duration or 0,
                        stream_url=t.getStreamURL(), thumb_url=thumb,
                    )
                    results.append((track, t.ratingKey))

        add(lib.searchTracks(title=query, maxresults=5))
        if len(results) < 5:
            for ar in lib.searchArtists(title=query, maxresults=2):
                add(ar.tracks()[:5])
        if len(results) < 5:
            for al in lib.searchAlbums(title=query, maxresults=2):
                add(al.tracks()[:5])
        return results

    # ── Group ──────────────────────────────────────────────────────────────────

    playlist = app_commands.Group(name="playlist", description="Create and manage custom playlists")

    # create

    @playlist.command(name="create", description="Create a new empty custom playlist")
    @app_commands.describe(name="Name for the new playlist")
    async def playlist_create(self, interaction: discord.Interaction, name: str):
        if _load_playlist(interaction.guild.id, name):
            await interaction.response.send_message(f"A playlist named **{name}** already exists.", ephemeral=True)
            return
        data = {"name": name, "created_by": str(interaction.user), "created_at": datetime.utcnow().isoformat(), "tracks": []}
        _save_playlist(interaction.guild.id, data)
        await interaction.response.send_message(f"✅ Created playlist **{name}**. Add tracks with `/playlist add`.")

    # add

    @playlist.command(name="add", description="Search Plex and add a track to a playlist")
    @app_commands.describe(name="Playlist to add to", query="Track to search for")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_add(self, interaction: discord.Interaction, name: str, query: str):
        data = _load_playlist(interaction.guild.id, name)
        if data is None:
            await interaction.response.send_message(f"Playlist **{name}** not found. Create it first with `/playlist create`.", ephemeral=True)
            return

        await interaction.response.defer()
        results = self._search_with_keys(query)
        if not results:
            await interaction.followup.send(f"No tracks found for **{query}**.")
            return

        if len(results) == 1:
            track, key = results[0]
        else:
            view = TrackPickForPlaylist(interaction.user.id, results, interaction.guild.id, name)
            await interaction.followup.send(embed=view.build_embed(name), view=view)
            return

        data["tracks"].append(_track_to_dict(track, key))
        _save_playlist(interaction.guild.id, data)
        await interaction.followup.send(
            f"➕ Added **{track.title}** — {track.artist} to **{name}** ({len(data['tracks'])} tracks)."
        )

    # remove

    @playlist.command(name="remove", description="Remove a track from a playlist by its number")
    @app_commands.describe(name="Playlist name", position="Track number shown in /playlist show")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_remove(self, interaction: discord.Interaction, name: str, position: int):
        data = _load_playlist(interaction.guild.id, name)
        if data is None:
            await interaction.response.send_message(f"Playlist **{name}** not found.", ephemeral=True)
            return
        if not (1 <= position <= len(data["tracks"])):
            await interaction.response.send_message(
                f"Invalid position. Playlist has {len(data['tracks'])} tracks.", ephemeral=True
            )
            return
        removed = data["tracks"].pop(position - 1)
        _save_playlist(interaction.guild.id, data)
        await interaction.response.send_message(f"🗑️ Removed **{removed['title']}** — {removed['artist']} from **{name}**.")

    # rename

    @playlist.command(name="rename", description="Rename a custom playlist")
    @app_commands.describe(name="Current playlist name", new_name="New name")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_rename(self, interaction: discord.Interaction, name: str, new_name: str):
        data = _load_playlist(interaction.guild.id, name)
        if data is None:
            await interaction.response.send_message(f"Playlist **{name}** not found.", ephemeral=True)
            return
        if _load_playlist(interaction.guild.id, new_name):
            await interaction.response.send_message(f"A playlist named **{new_name}** already exists.", ephemeral=True)
            return
        _delete_playlist(interaction.guild.id, name)
        data["name"] = new_name
        _save_playlist(interaction.guild.id, data)
        await interaction.response.send_message(f"✏️ Renamed **{name}** → **{new_name}**.")

    # delete

    @playlist.command(name="delete", description="Delete a custom playlist")
    @app_commands.describe(name="Playlist to delete")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        if not _load_playlist(interaction.guild.id, name):
            await interaction.response.send_message(f"Playlist **{name}** not found.", ephemeral=True)
            return
        view = ConfirmDeleteView(interaction.user.id, interaction.guild.id, name)
        await interaction.response.send_message(
            f"⚠️ Delete playlist **{name}**? This cannot be undone.", view=view, ephemeral=True
        )

    # list

    @playlist.command(name="list", description="Show all custom playlists for this server")
    async def playlist_list(self, interaction: discord.Interaction):
        playlists = _list_playlists(interaction.guild.id)
        if not playlists:
            await interaction.response.send_message("No custom playlists yet. Use `/playlist create` to make one.")
            return
        embed = discord.Embed(title="📋 Custom Playlists", color=discord.Color.blurple())
        for pl in playlists:
            n = len(pl["tracks"])
            embed.add_field(name=pl["name"], value=f"{n} track{'s' if n != 1 else ''}  •  by {pl['created_by']}", inline=False)
        await interaction.response.send_message(embed=embed)

    # show

    @playlist.command(name="show", description="Show all tracks in a playlist")
    @app_commands.describe(name="Playlist to view")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_show(self, interaction: discord.Interaction, name: str):
        data = _load_playlist(interaction.guild.id, name)
        if data is None:
            await interaction.response.send_message(f"Playlist **{name}** not found.", ephemeral=True)
            return
        tracks = data["tracks"]
        if not tracks:
            await interaction.response.send_message(f"**{name}** is empty.")
            return

        embed = discord.Embed(
            title=f"📋 {data['name']}",
            description=f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}  •  by {data['created_by']}",
            color=discord.Color.blurple(),
        )
        chunk = []
        for i, t in enumerate(tracks, 1):
            ms = t["duration_ms"]
            mins, secs = divmod(ms // 1000, 60)
            chunk.append(f"`{i:>2}.` **{t['title']}** — {t['artist']}  `{mins}:{secs:02d}`")
            if len(chunk) == 10:
                embed.add_field(name="\u200b", value="\n".join(chunk), inline=False)
                chunk = []
        if chunk:
            embed.add_field(name="\u200b", value="\n".join(chunk), inline=False)
        await interaction.response.send_message(embed=embed)

    # play

    @playlist.command(name="play", description="Play a custom playlist now")
    @app_commands.describe(name="Playlist to play")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_play(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        if not interaction.user.voice:
            await interaction.followup.send("You must be in a voice channel first.", ephemeral=True)
            return

        data = _load_playlist(interaction.guild.id, name)
        if data is None:
            await interaction.followup.send(f"Playlist **{name}** not found.")
            return
        if not data["tracks"]:
            await interaction.followup.send(f"Playlist **{name}** is empty.")
            return

        tracks, failed = [], 0
        for entry in data["tracks"]:
            t = _dict_to_track(self.plex, entry)
            if t:
                tracks.append(t)
            else:
                failed += 1

        if not tracks:
            await interaction.followup.send("Could not load any tracks from this playlist.")
            return

        vc = await self._connect_voice(interaction)
        if not vc:
            return

        state = self._state(interaction.guild.id)
        state.text_channel = interaction.channel
        first, rest = tracks[0], tracks[1:]
        for t in rest:
            state.queue.append(t)

        suffix = f" ({failed} skipped)" if failed else ""
        if vc.is_playing() or vc.is_paused():
            state.queue.appendleft(first)
            await interaction.followup.send(f"📋 Queued **{len(tracks)} tracks** from **{name}**{suffix}.")
        else:
            state.current = first
            vc.play(
                self.music_cog._make_source(first, state.volume),
                after=lambda e: self.music_cog._play_next(state, vc),
            )
            await self.music_cog._update_presence(first)
            await interaction.followup.send(
                content=f"📋 Playing **{name}** — {len(tracks)} tracks{suffix}",
                embed=self.music_cog._now_playing_embed(first, state),
            )

    # queue

    @playlist.command(name="queue", description="Add a custom playlist to the current queue")
    @app_commands.describe(name="Playlist to queue")
    @app_commands.autocomplete(name=playlist_autocomplete)
    async def playlist_queue(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        data = _load_playlist(interaction.guild.id, name)
        if data is None:
            await interaction.followup.send(f"Playlist **{name}** not found.")
            return
        if not data["tracks"]:
            await interaction.followup.send(f"Playlist **{name}** is empty.")
            return

        tracks = [t for entry in data["tracks"] if (t := _dict_to_track(self.plex, entry))]
        if not tracks:
            await interaction.followup.send("Could not load any tracks.")
            return

        state = self._state(interaction.guild.id)
        for t in tracks:
            state.queue.append(t)
        await interaction.followup.send(f"➕ Queued **{len(tracks)} tracks** from **{name}**.")


# ── Confirm delete view ────────────────────────────────────────────────────────

class ConfirmDeleteView(ui.View):
    def __init__(self, author_id: int, guild_id: int, playlist_name: str):
        super().__init__(timeout=20)
        self.author_id = author_id
        self.guild_id = guild_id
        self.playlist_name = playlist_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @ui.button(label="✅ Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        _delete_playlist(self.guild_id, self.playlist_name)
        self.stop()
        await interaction.response.edit_message(
            content=f"🗑️ Playlist **{self.playlist_name}** deleted.", view=None
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


# ── Track picker for /playlist add (multi-result) ─────────────────────────────

class TrackPickForPlaylist(ui.View):
    def __init__(self, author_id: int, results: list[tuple[Track, int]], guild_id: int, playlist_name: str):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.results = results
        self.guild_id = guild_id
        self.playlist_name = playlist_name

        options = [
            discord.SelectOption(label=t.title[:100], description=f"{t.artist} — {t.album}"[:100], value=str(i))
            for i, (t, _) in enumerate(results)
        ]
        select = ui.Select(placeholder="Choose a track to add…", options=options)
        select.callback = self.on_select
        self.add_item(select)

    def build_embed(self, playlist_name: str) -> discord.Embed:
        embed = discord.Embed(title=f"Add to \"{playlist_name}\"", description="Select a track:", color=discord.Color.blurple())
        for i, (t, _) in enumerate(self.results, 1):
            embed.add_field(name=f"{i}. {t.title}", value=f"{t.artist}  •  *{t.album}*  •  `{t.duration_str}`", inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def on_select(self, interaction: discord.Interaction):
        track, key = self.results[int(interaction.data["values"][0])]
        data = _load_playlist(self.guild_id, self.playlist_name)
        if data is None:
            await interaction.response.edit_message(content="Playlist no longer exists.", view=None, embed=None)
            return
        data["tracks"].append(_track_to_dict(track, key))
        _save_playlist(self.guild_id, data)
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                description=f"➕ Added **{track.title}** — {track.artist} to **{self.playlist_name}** ({len(data['tracks'])} tracks).",
                color=discord.Color.green(),
            ),
            view=None,
        )


async def setup(bot: commands.Bot):
    music_cog = bot.cogs.get("Music")
    if music_cog is None:
        raise RuntimeError("Playlists cog requires Music cog to be loaded first.")
    await bot.add_cog(Playlists(bot, music_cog))
