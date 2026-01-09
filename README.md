# 🎶 Random Playlist Player (SYTKTV)

A Python program that randomly plays content from YouTube and Spotify playlists. YouTube videos open in your browser, while Spotify tracks open in the desktop app.

## Features

- 🎬 Fetches videos from public YouTube playlists
- 🎵 Fetches tracks from public Spotify playlists
- 🎲 Randomly selects which platform to play from
- 🌐 Opens YouTube videos in your default browser
- 🖥️ Opens Spotify tracks in the desktop app

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Spotify API Credentials

To access Spotify playlists, you need API credentials:

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click "Create App"
4. Fill in the app details (name, description)
5. Copy your **Client ID** and **Client Secret**

### 3. Configure Environment (Optional)

Copy `.env.example` to `.env` and fill in your values:

```bash
copy .env.example .env
```

Edit `.env`:
```
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
YOUTUBE_PLAYLIST_URL=https://www.youtube.com/playlist?list=PLxxxxx
SPOTIFY_PLAYLIST_URL=https://open.spotify.com/playlist/xxxxx
```

Or you can enter these values when prompted by the program.

## Usage

Open spotify desktop app, open lyrics plus (spicetify) and F12

A "Next Up" window opens automatically to show the upcoming queue (replaces next_up.txt).

Run the program in powershell in the root of the program:

```bash
python playlist_player.py
```

### Commands

| Key | Action |
|-----|--------|
| `Enter` | Play a random track from either playlist |
| `y` | Play a random YouTube video |
| `s` | Play a random Spotify track |
| `q` | Quit the program |

## How It Works

1. **YouTube**: Uses `yt-dlp` to extract video information from the playlist without downloading
2. **Spotify**: Uses the Spotify Web API via `spotipy` to fetch playlist tracks
3. **Playback**: 
   - YouTube videos open in your default web browser
   - Spotify tracks open using the `spotify:` URI scheme (requires Spotify desktop app)

## Requirements

- Python 3.10+
- Spotify Desktop App (for Spotify playback)
- Internet connection
- Spotify Developer account (free)

## Notes

- Both playlists must be **public** to be accessible
- YouTube extraction doesn't require any API keys
- Spotify requires API credentials but doesn't need user authentication for public playlists
