"""Data models for the playlist player application."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Track:
    """Represents a track from either platform."""

    title: str
    artist: str
    platform: str  # 'youtube' or 'spotify'
    url: str
    uri: Optional[str] = None  # Spotify URI for desktop app
    duration: Optional[float] = None  # duration in seconds
    # Who added this track to the playlist (Spotify): user id and resolved display name
    added_by_id: Optional[str] = None
    added_by_name: Optional[str] = None
    added_at: Optional[str] = None
