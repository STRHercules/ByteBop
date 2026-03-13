import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


class PlexBot(commands.Bot):
    async def setup_hook(self):
        await self.load_extension("music")
        print("🎵 Music cog loaded")
        await self.load_extension("playlists")
        print("📋 Playlists cog loaded")
        await self.load_extension("search")
        print("🔍 Search cog loaded")

        # Sync slash commands globally.
        # NOTE: Global sync can take up to 1 hour to propagate.
        # For instant updates during development, use guild sync instead:
        #   await self.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))
        await self.tree.sync()
        print("✅ Slash commands synced")

        # Start the presence refresh loop
        self.refresh_presence.start()

    @tasks.loop(minutes=30)
    async def refresh_presence(self):
        """Refresh Plex stats in the bot's presence every 30 minutes."""
        music_cog = self.cogs.get("Music")
        if music_cog:
            await music_cog._set_plex_presence()

    @refresh_presence.before_loop
    async def before_refresh_presence(self):
        await self.wait_until_ready()

    async def on_ready(self):
        print(f"✅ Logged in as {self.user} (ID: {self.user.id})")
        print(f"📡 Streaming across {len(self.guilds)} server(s) simultaneously")
        # Set presence immediately on ready (the loop also fires every 30 min)
        music_cog = self.cogs.get("Music")
        if music_cog:
            await music_cog._set_plex_presence()


bot = PlexBot(command_prefix="!", intents=intents, help_command=None)


# Owner-only prefix command to force a command tree re-sync (useful during dev)
@bot.command(name="sync", hidden=True)
@commands.is_owner()
async def sync(ctx, guild_id: int = None):
    if guild_id:
        guild = discord.Object(id=guild_id)
        bot.tree.copy_global_to(target=guild)
        synced = await bot.tree.sync(guild=guild)
        await ctx.send(f"Synced {len(synced)} command(s) to guild {guild_id}.")
    else:
        synced = await bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} command(s) globally (may take up to 1 hour).")


@bot.tree.command(name="help", description="Show all ByteBop commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎵 ByteBop — Command Reference",
        description="Stream your Plex library into Discord voice channels.",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="🔍 Search",
        value=(
            "`/search` — Paginated Plex search with actions\n"
            "↳ Select a track to **Play**, **Add to Playlist**, **Search Artist**, or **Search Album**\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎵 Playback",
        value=(
            "`/play` — Search & play a track (dropdown picker if multiple results)\n"
            "`/playalbum` — Queue a full album\n"
            "`/pause` · `/resume` · `/skip` · `/stop`\n"
            "`/nowplaying` — Show current track details\n"
            "`/loop` — Toggle loop mode\n"
            "`/volume` — Set volume (0–100)\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 Queue",
        value="`/queue` — Show the queue  ·  `/clear` — Clear the queue",
        inline=False,
    )
    embed.add_field(
        name="📼 Plex Playlists",
        value="`/plexlist` — Play a playlist directly from your Plex library",
        inline=False,
    )
    embed.add_field(
        name="📋 Custom Playlists",
        value=(
            "`/playlist create` — Create a new playlist\n"
            "`/playlist add` — Add a track (with Plex search + autocomplete)\n"
            "`/playlist remove` — Remove a track by number\n"
            "`/playlist rename` · `/playlist delete`\n"
            "`/playlist list` — Show all playlists\n"
            "`/playlist show` — View tracks in a playlist\n"
            "`/playlist play` · `/playlist queue` — Play or queue a playlist\n"
        ),
        inline=False,
    )
    embed.set_footer(text="Playlist name fields support autocomplete — just start typing.")
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ValueError("DISCORD_TOKEN not set in .env file")
    bot.run(token)
