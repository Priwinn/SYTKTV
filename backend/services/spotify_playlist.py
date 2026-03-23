"""Spotify playlist provider."""

import re
from typing import Optional

from models import Track


class SpotifyPlaylist:
    """Handles Spotify playlist extraction."""

    def __init__(self, playlist_url: str, client_id: str, client_secret: str):
        self.playlist_url = playlist_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.tracks: list[Track] = []
        # Cache mapping Spotify user id -> display name (to avoid repeated API calls)
        self._user_display_cache: dict[str, str] = {}

    def extract_playlist_id(self) -> Optional[str]:
        """Extract playlist ID from URL."""
        patterns = [
            r"playlist/([a-zA-Z0-9]+)",
            r"playlist:([a-zA-Z0-9]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, self.playlist_url)
            if match:
                return match.group(1)
        return None

    def fetch_tracks(self) -> list[Track]:
        """Fetch tracks from the playlist using Spotipy."""
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            playlist_id = self.extract_playlist_id()
            if not playlist_id:
                print("Error: Could not extract playlist ID from Spotify URL")
                return []

            auth_manager = SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
            sp = spotipy.Spotify(auth_manager=auth_manager)

            results = sp.playlist_tracks(playlist_id)

            while results:
                for item in results["items"]:
                    track = item.get("track")
                    if track:
                        artists = ", ".join([a["name"] for a in track.get("artists", [])])
                        # Spotify API gives 'added_by' and 'added_at' on the playlist item.
                        added_by = item.get("added_by") or {}
                        added_by_id = added_by.get("id") if isinstance(added_by, dict) else None
                        added_at = item.get("added_at")
                        added_by_name = None
                        # Try to resolve display name via Spotify API (cached).
                        if added_by_id:
                            try:
                                if added_by_id in self._user_display_cache:
                                    added_by_name = self._user_display_cache[added_by_id]
                                else:
                                    user = sp.user(added_by_id)
                                    name = user.get("display_name") if isinstance(user, dict) else None
                                    if name:
                                        added_by_name = name
                                        self._user_display_cache[added_by_id] = name
                            except Exception:
                                added_by_name = added_by_id
                        track_id = track.get("id", "")

                        self.tracks.append(
                            Track(
                                title=track.get("name", "Unknown Title"),
                                artist=artists or "Unknown Artist",
                                platform="spotify",
                                url=track.get("external_urls", {}).get("spotify", ""),
                                uri=f"spotify:track:{track_id}",
                                duration=(track.get("duration_ms") / 1000.0)
                                if track.get("duration_ms")
                                else None,
                                added_by_id=added_by_id,
                                added_by_name=added_by_name,
                                added_at=added_at,
                            )
                        )

                if results["next"]:
                    results = sp.next(results)
                else:
                    results = None

            print(f"✓ Loaded {len(self.tracks)} tracks from Spotify playlist")
            return self.tracks

        except Exception as e:
            print(f"Error fetching Spotify playlist: {e}")
            return []
