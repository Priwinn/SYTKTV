# 🎶 Spotify/Youtube KTV (SYTKTV)

A Python program that queues content from YouTube and Spotify playlists.

## Features

- 🎬 Fetches videos from public YouTube playlists
- 🎵 Fetches tracks from public Spotify playlists
- 🎲 GUI that displays the queue, allowing for shuffling, reordering, pausing and more
- 🌐 Opens YouTube videos and Spotify songs in your default browser.
- Focuses the Spotify desktop app if present to display lyrics.
- Uses the Karaoke Monster browser extension for Youtube lyrics and voice removal for both platforms.
- Persists a play count list, only media with the minimum number of play counts will be added to the queue (can delete file to reset).


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

### 3. Configure Environment

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
You can use collaboration invitation links. QR codes will be generated pointing to the links. Be advised that as of Jan 2026, Spotify playlist collaboration links expire while Youtube ones do not. 

Or you can enter these values when prompted by the program (Not implemented)

### 4. Install spicetify and modded lyrics plus (Optional)
In order to use extended lyrics options, including romanization of Korean, Chinese and Japanese, follow instructions to install the extended spicetify lyrics plus:

https://github.com/Priwinn/extendedLyrics

### 5. Install Karaoke Monster and uBlock origin lite (Optional)
https://chromewebstore.google.com/detail/karaoke-monster/impekelmmfmbnjfjadmjnfclgkacaekn
https://chromewebstore.google.com/detail/ublock-origin-lite/ddkjiahejlhfcafbddmgiahcphecmpfh


## Usage

Open spotify desktop app, open lyrics plus (spicetify) and F12 or open your preferred lyrics option (untested but should work)

Set display resolution to 1920x1080 and scale to 125% (optional, if using voice removal)

Run the program in powershell in the root of the program:

```bash
python playlist_player.py
```

A "Next Up" window opens automatically to show the upcoming queue and some useful KTV commands

If using the voice removal extension or youtube lyrics. 

## How It Works

1. **YouTube**: Uses `yt-dlp` to extract video information from the playlist without downloading
2. **Spotify**: Uses the Spotify Web API via `spotipy` to fetch playlist tracks
3. **Playback**: 
   - Videos and songs open in your default web browser

## Requirements

- Python 3.10+
- Spotify Desktop App (for Spotify playback)
- Internet connection
- Spotify Developer account (free)
- Spotify premium (optional?): this app was only tested for spotify premium and youtube with ublock origin lite

## Notes

- Both playlists must be **public** to be accessible
- YouTube extraction doesn't require any API keys
- Spotify requires API credentials but doesn't need user authentication for public playlists
- Avoid messing with the fullscreen, as a rule of thumb fullscreen should be active at all times. Tip: use Alt+TAB and/or a second monitor to interact with the menu
- Youtube lyrics are not very reliable, it works best with official MVs that do not include non-music segments in the middle of the video (initial or tailing ones can be adjusted using the track delay)
- Rule of thumb: if the lyrics are more important than the video use spotify, otherwise use youtube
- The app sets a timer to in order to know when to play the next song. Thus users should only pause and play using the GUI

## Test environment
This app was tested with Windows 11 with display resolution of 1920x1080 and scale 125%.

## Disclaimer

This app was majorly coded with the help of Copilot using Opus 4.5 and GPT5 mini, as such it is very rough around the edges.
