# 🎶 Spotify / YouTube KTV (SYTKTV)

A small Python utility that queues content from YouTube and Spotify playlists and opens them for KTV-style playback.

## Features

- 🎬 Fetches videos from public YouTube playlists
- 🎵 Fetches tracks from public Spotify playlists
- 🎲 Small GUI showing the upcoming queue, supports shuffle, drag-reorder, and play/pause controls
- 🌐 Opens YouTube videos and Spotify songs in your default browser and (optionally) focuses the Spotify desktop app to show lyrics
- ✨ Integrates with Karaoke Monster (browser extension) for voice removal and lyrics display when available
- 💾 Persists a play-count file so tracks with the lowest counts are prioritized (delete the file to reset)


## Setup

### 1. Install dependencies

Install runtime dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

Notes:
- The pinned `requirements.txt` contains the packages used during development. Some GUI features (image preview for QR codes) require `Pillow`/`qrcode` which are included.
- On Windows, pixel-based automation (used by VR/voice-removal helpers) is resolution/scale dependent — see the **Test environment** note below.

### 2. Spotify API Credentials

To access Spotify playlists, you need API credentials:

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click "Create App"
4. Fill in the app details (name, description)
5. Copy your **Client ID** and **Client Secret**

### 3. Configure environment

Copy `.env.example` to `.env` (or create a `.env` file) and fill in the values:

```bash
copy .env.example .env
```

Then edit `.env` and set:

```
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
YOUTUBE_PLAYLIST_URL=https://www.youtube.com/playlist?list=PLxxxxx
SPOTIFY_PLAYLIST_URL=https://open.spotify.com/playlist/xxxxx
```

If the environment variables are not present, the program will prompt you to enter playlist URLs and (if needed) Spotify credentials at startup.

You can use Spotify collaboration links or standard playlist links. Note: some Spotify collaboration links may expire — verify link behavior with Spotify if you rely on invite links.

### 4. Install spicetify and modded lyrics plus (Optional)
In order to use extended lyrics options, including romanization of Korean, Chinese and Japanese, follow instructions to install the extended spicetify lyrics plus:

https://github.com/Priwinn/extendedLyrics

### 5. Install Karaoke Monster and uBlock origin lite (Optional)

Make sure the karaoke monster extension is the first extension to the left of the extension icon (you might still need to adjust pixel values in vr methods)

https://chromewebstore.google.com/detail/karaoke-monster/impekelmmfmbnjfjadmjnfclgkacaekn
https://chromewebstore.google.com/detail/ublock-origin-lite/ddkjiahejlhfcafbddmgiahcphecmpfh


## Usage

- Open the Spotify desktop app if you plan to use Spotify playback/lyrics.
- (Optional) If you use spicetify/lyrics plugins, open them before running the program.
- For best results when using pixel-based automation (voice removal helpers) set display resolution to 1920×1080 and Windows scale to 125% — otherwise pixel coordinates used by the helpers may need manual adjustment.

Run the program in powershell in the root of the program:

```bash
python playlist_player.py
```

A "Next Up" window opens automatically to show the upcoming queue and controls.

## How It Works

1. **YouTube**: Uses `yt-dlp` to extract video information from the playlist without downloading
2. **Spotify**: Uses the Spotify Web API via `spotipy` to fetch playlist tracks
3. **Playback**: 
   - Videos and songs open in your default web browser

## Requirements

- Python 3.10+
- Spotify Desktop App (for Spotify lyrics, optional)
- Internet connection
- Spotify Developer account (free) for API credentials
- Spotify Premium not required by the code, but some playback/lyrics workflows were tested with Premium and may behave differently on free accounts

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

This app was majorly coded with the help of Copilot using Opus 4.5 and GPT5 mini, as such, it is very rough around the edges.
