"""YouTube playlist provider."""

import re
from typing import Optional

from models import Track


class YouTubePlaylist:
    """Handles YouTube playlist extraction."""

    def __init__(self, playlist_url: str):
        self.playlist_url = playlist_url
        self.videos: list[Track] = []

    def extract_playlist_id(self) -> Optional[str]:
        """Extract playlist ID from URL."""
        patterns = [
            r"list=([a-zA-Z0-9_-]+)",
            r"playlist\?list=([a-zA-Z0-9_-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, self.playlist_url)
            if match:
                return match.group(1)
        return None

    def fetch_videos(self) -> list[Track]:
        """Fetch videos from the playlist using yt-dlp."""
        try:
            import yt_dlp

            playlist_id = self.extract_playlist_id()
            if not playlist_id:
                print("Error: Could not extract playlist ID from URL")
                return []

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(self.playlist_url, download=False)

                if result and "entries" in result:
                    for entry in result["entries"]:
                        if entry:
                            video_id = entry.get("id", "")
                            title = entry.get("title", "Unknown Title")
                            uploader = entry.get("uploader", "Unknown Artist")
                            duration = entry.get("duration")
                            # If duration is not present, try to fetch video info.
                            if duration is None and video_id:
                                try:
                                    info = ydl.extract_info(
                                        f"https://www.youtube.com/watch?v={video_id}",
                                        download=False,
                                    )
                                    duration = info.get("duration")
                                except Exception:
                                    duration = None

                            self.videos.append(
                                Track(
                                    title=title,
                                    artist=uploader,
                                    platform="youtube",
                                    url=f"https://www.youtube.com/watch?v={video_id}",
                                    duration=float(duration) if duration else None,
                                )
                            )

            print(f"✓ Loaded {len(self.videos)} videos from YouTube playlist")
            return self.videos

        except Exception as e:
            print(f"Error fetching YouTube playlist: {e}")
            return []
