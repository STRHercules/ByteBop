# 🎵 ByteBop — Plex Discord Music Bot

A self-hosted Discord music bot that streams directly from your personal Plex Media Server. All slash commands, rich embeds, paginated search, custom playlists, and fully independent multi-server streaming.

---

## ✨ Features

- **Slash commands** — all commands are native Discord `/` commands with descriptions, parameter hints, and autocomplete
- **Plex integration** — searches tracks, artists, and albums simultaneously for the best match
- **Interactive search** — paginated results (10 per page) with a dropdown to Play, Add to Playlist, Search Artist, or Search Album
- **Custom playlists** — create and manage per-server playlists stored as local JSON files, independent of Plex
- **Plex playlists** — play playlists that live directly on your Plex server
- **Multi-server streaming** — each Discord server gets a fully independent queue, volume, and voice connection simultaneously
- **Now Playing presence** — bot status updates live with the current track
- **Per-server state** — volume, loop mode, and queue are isolated per server

---

## 📋 Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.12 or 3.13 recommended |
| FFmpeg | Must be on your system PATH |
| Plex Media Server | Must have a **Music** library (type: artist) |
| Discord Bot Token | From the Discord Developer Portal |

---

## 🚀 Setup

### 1. Install FFmpeg

FFmpeg handles all audio transcoding. It must be installed and available on your PATH.

**Windows:**
Download from https://ffmpeg.org/download.html, extract, and add the `bin/` folder to your system PATH.
Verify with: `ffmpeg -version`

**macOS (Homebrew):**
```bash
brew install ffmpeg
```

**Ubuntu / Debian:**
```bash
sudo apt update && sudo apt install ffmpeg
```

---

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

`requirements.txt` includes:
```
discord.py[voice]>=2.3.0
plexapi>=4.15.0
python-dotenv>=1.0.0
PyNaCl>=1.5.0
```

> **Note:** PyNaCl is required for Discord voice encryption. Without it, the bot will connect but produce no audio.

---

### 3. Create a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** → give it a name (e.g. `ByteBop`)
3. Go to the **Bot** tab → click **Add Bot**
4. Under **Token** → click **Reset Token** and copy it (save this for `.env`)
5. Under **Privileged Gateway Intents**, enable:
   - ✅ **Message Content Intent**
   - ✅ **Server Members Intent** (recommended)
6. Go to **OAuth2 → URL Generator**:
   - **Scopes:** `bot`, `applications.commands`
   - **Bot Permissions:** `Connect`, `Speak`, `Send Messages`, `Read Message History`, `Use Voice Activity`, `Add Reactions`, `Embed Links`
7. Copy the generated URL, open it in your browser, and invite the bot to your server

> **applications.commands** scope is required for slash commands to register.

---

### 4. Get your Plex Token

Your Plex token authenticates the bot to your Plex server.

**Method 1 — Via Plex Web:**
1. Sign in to Plex Web and browse to any media item
2. Click the **⋮** menu → **Get Info** → **View XML**
3. In the URL bar, find `X-Plex-Token=XXXXXXXXXX` — copy that value

**Method 2 — Official guide:**
https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

---

### 5. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:
```env
# Discord
DISCORD_TOKEN=your_discord_bot_token

# Plex
PLEX_URL=http://your-plex-ip:32400
PLEX_TOKEN=your_plex_token
```

**PLEX_URL examples:**
| Scenario | Example URL |
|---|---|
| Local network | `http://192.168.1.100:32400` |
| Same machine | `http://localhost:32400` |
| Remote / domain | `https://plex.yourdomain.com:32400` |

> Never commit `.env` to version control. It is listed in `.gitignore` by default.

---

### 6. Run the bot

```bash
python bot.py
```

On first startup you should see:
```
Connecting to Plex at http://localhost:32400 ...
Connected to Plex: Your Server Name
Libraries: [('Music', 'artist'), ...]
🎵 Music cog loaded
📋 Playlists cog loaded
🔍 Search cog loaded
✅ Slash commands synced
✅ Logged in as ByteBop#0000
📡 Streaming across 1 server(s) simultaneously
```

> **Slash command propagation:** Global command sync can take up to **1 hour** to appear in all servers. For instant sync during development, use the owner-only prefix command `!sync <guild_id>` after the bot is online.

---

## 💬 Commands

All commands are Discord slash commands (`/command`). Parameter names and descriptions are shown natively in Discord's command menu.

---

### 🔍 Search

| Command | Description |
|---|---|
| `/search <query>` | Search your Plex library with full paginated results |

`/search` is the most powerful way to find music. Results are displayed 10 per page in a rich embed with **◀ Prev** / **Next ▶** pagination. Selecting a track from the dropdown opens an action menu:

| Action | Description |
|---|---|
| ▶ **Play** | Play immediately, or add to queue if something is already playing |
| 📋 **Add to Playlist** | Add the track to any of your custom playlists |
| 🎤 **Search Artist** | Show all tracks by this artist (paginated) |
| 💿 **Search Album** | Show all tracks from this album |
| ← **Back** | Return to the search results |

---

### 🎵 Playback

| Command | Parameters | Description |
|---|---|---|
| `/play` | `query` | Search Plex and play a track. Shows a dropdown picker if multiple results are found |
| `/playalbum` | `query` | Search for an album and queue all its tracks |
| `/plexlist` | `name` | Play a playlist that exists in your Plex library |
| `/pause` | — | Pause the current track |
| `/resume` | — | Resume a paused track |
| `/skip` | — | Skip to the next track in queue |
| `/stop` | — | Stop playback and disconnect from voice |
| `/nowplaying` | — | Show a rich embed with details on the current track |
| `/loop` | — | Toggle loop mode (repeats the current track) |
| `/volume` | `level` (0–100) | Set playback volume. Changes apply immediately |

**How `/play` search works:**
The bot searches your Plex Music library in three passes:
1. Track title match
2. Artist name match → returns their top tracks
3. Album name match → returns the album's tracks

Results are deduplicated. If only one match is found, it plays immediately. If multiple are found, a dropdown picker is shown.

---

### 📋 Queue

| Command | Description |
|---|---|
| `/queue` | Show the current playback queue (up to 10 tracks previewed) |
| `/clear` | Clear all tracks from the queue (does not stop current track) |

---

### 📋 Custom Playlists

Custom playlists are stored locally as JSON files in `./playlists/<guild_id>/`. They are per-server and persist across bot restarts. Tracks are stored by Plex `ratingKey` for reliable resolution.

All `name` parameters support **autocomplete** — Discord will suggest matching playlist names as you type.

| Command | Parameters | Description |
|---|---|---|
| `/playlist create` | `name` | Create a new empty playlist |
| `/playlist add` | `name`, `query` | Search Plex and add a track to a playlist |
| `/playlist remove` | `name`, `position` | Remove a track by its number (see `/playlist show`) |
| `/playlist rename` | `name`, `new_name` | Rename a playlist |
| `/playlist delete` | `name` | Delete a playlist (requires confirmation) |
| `/playlist list` | — | Show all custom playlists for this server |
| `/playlist show` | `name` | Show all tracks in a playlist with their numbers |
| `/playlist play` | `name` | Play a custom playlist now |
| `/playlist queue` | `name` | Add all tracks from a playlist to the current queue |

---

### 🛠️ Utility

| Command | Parameters | Description |
|---|---|---|
| `/help` | — | Show the full command reference |
| `/debug` | `query` (optional) | Show Plex connection info. Pass a query to see raw search results — useful for diagnosing why a track isn't being found |

**Owner-only prefix commands** (not slash commands):

| Command | Description |
|---|---|
| `!sync` | Re-sync slash commands globally |
| `!sync <guild_id>` | Instantly sync slash commands to a specific server (use during development) |

---

## 📁 Project Structure

```
ByteBop/
├── bot.py              # Bot entrypoint, cog loader, slash command sync
├── music.py            # Core playback engine — all playback slash commands
├── playlists.py        # Custom playlist management — /playlist command group
├── search.py           # /search command with paginated UI and action views
│
├── playlists/          # Auto-created on first playlist save
│   └── <guild_id>/
│       └── <playlist_name>.json
│
├── .env                # Your secrets — NEVER commit this
├── .env.example        # Template for .env
├── requirements.txt
└── README.md
```

---

## 🏗️ Architecture Notes

### Multi-server streaming
Each Discord server (guild) has its own `GuildState` object containing an independent queue, current track, volume level, loop toggle, and text channel reference. The bot can stream different music to multiple voice channels across multiple servers simultaneously without any state bleed between them.

### Slash command sync
Commands are synced globally on startup via `bot.tree.sync()`. Global sync can take up to 1 hour to propagate across all Discord servers. During development, use `!sync <guild_id>` for instant guild-scoped sync.

### Plex library resolution
On startup the bot scans all Plex library sections and prefers a section named **"Music"** with type `artist`. If no section is named "Music", it falls back to the first `artist`-type section found. Use `/debug` to confirm which library is being used.

### Custom playlist track storage
Tracks added to custom playlists are stored with their Plex `ratingKey`. When a playlist is played, each track is re-fetched from Plex by rating key to get a fresh stream URL. This means playlists remain valid even if you rename or move files in Plex, as long as the media exists in your library.

---

## 🔧 Troubleshooting

**Slash commands don't appear in Discord**
- Make sure you invited the bot with the `applications.commands` scope
- Run `!sync <your_guild_id>` for instant sync, or wait up to 1 hour for global propagation
- Confirm the bot is online and printed "Slash commands synced" at startup

**Bot connects to voice but no audio plays**
- Verify FFmpeg is installed: `ffmpeg -version`
- Confirm PyNaCl is installed: `pip show PyNaCl`
- Check that your `PLEX_URL` is reachable from the machine running the bot (try opening it in a browser)

**"No Music library found" error**
- Your Plex server must have a library of type `artist` (the standard Music library type)
- Run `/debug` in Discord to see all detected library names and types
- The bot looks for a library named "Music" first — if yours is named differently, it will still fall back to the first artist-type library found

**Tracks not found with `/play` or `/search`**
- Run `/debug <query>` to see raw Plex search results broken down by track, artist, and album
- Check for special characters or diacritics in track/artist names (e.g. "MÓNACO" vs "MONACO")
- Ensure the track has been fully scanned into your Plex Music library

**Plex authentication errors**
- Double-check `PLEX_TOKEN` and `PLEX_URL` in `.env`
- If your Plex server uses HTTPS with a self-signed certificate, add `session=requests.Session()` with SSL verification disabled to the `PlexServer()` call in `music.py`

**Custom playlist tracks fail to load**
- This happens when a track has been removed from Plex or its `ratingKey` has changed (rare, usually after a full library wipe and re-scan)
- Run `/playlist show <name>` to identify which tracks are affected, then remove and re-add them with `/playlist add`

---

## 📄 License

MIT — do whatever you want with it.
