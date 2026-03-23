"""Desktop-first player core: Spotify in desktop app, YouTube in browser."""

import os
import threading
import time

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from backend.player_core import RandomPlayer as BrowserHybridPlayer
from models import Track


class RandomPlayer(BrowserHybridPlayer):
	"""Player core that launches Spotify in desktop app and YouTube in browser."""

	def __init__(self):
		super().__init__()
		self._spotify_api: spotipy.Spotify | None = None
		self._spotify_oauth_manager: SpotifyOAuth | None = None
		self._spotify_auth_failed: bool = False
		self._spotify_device_id: str = os.getenv("SPOTIFY_DEVICE_ID", "").strip()
		self._spotify_auth_lock = threading.Lock()

	def _clear_autoplay_bookkeeping(self):
		"""Cancel and clear autoplay timer and pause bookkeeping."""
		if self._autoplay_timer:
			self._autoplay_timer.cancel()
			self._autoplay_timer = None
		self._autoplay_start_time = None
		self._autoplay_duration = None
		self._autoplay_remaining = None
		self._autoplay_paused = False

	def _spotify_uri_for_track(self, track: Track) -> str:
		"""Return a spotify:track URI when possible for desktop app launching."""
		if track.uri and track.uri.startswith("spotify:track:"):
			return track.uri

		url = track.url or ""
		if "spotify.com/track/" not in url:
			return ""

		# Example: https://open.spotify.com/track/<id>?si=...
		track_id = url.split("/track/", 1)[1].split("?", 1)[0].strip("/")
		if not track_id:
			return ""
		return f"spotify:track:{track_id}"

	def _spotify_auth_cache_path(self) -> str:
		"""Return cache path for Spotify user OAuth tokens."""
		env_cache = os.getenv("SPOTIFY_USER_CACHE_PATH", "").strip()
		if env_cache:
			cache_path = env_cache
		else:
			cache_path = os.path.join(self._project_root(), ".spotify_user_token_cache")

		cache_dir = os.path.dirname(cache_path)
		if cache_dir:
			os.makedirs(cache_dir, exist_ok=True)
		return cache_path

	def _spotify_redirect_uri(self) -> str:
		"""Return OAuth redirect URI for Spotify user auth."""
		return os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

	def _spotify_scope(self) -> str:
		"""Scopes required for playback control and navigation."""
		return (
			"user-read-playback-state "
			"user-modify-playback-state "
			"user-read-currently-playing"
		)

	def _build_spotify_oauth_manager(self) -> SpotifyOAuth:
		"""Build Spotify OAuth manager with playback scopes."""
		client_id = (self._spotify_client_id or os.getenv("SPOTIPY_CLIENT_ID", "")).strip()
		client_secret = (self._spotify_client_secret or os.getenv("SPOTIPY_CLIENT_SECRET", "")).strip()
		if not client_id or not client_secret:
			raise RuntimeError("Missing SPOTIPY_CLIENT_ID/SECRET")

		return SpotifyOAuth(
			client_id=client_id,
			client_secret=client_secret,
			redirect_uri=self._spotify_redirect_uri(),
			scope=self._spotify_scope(),
			open_browser=True,
			cache_path=self._spotify_auth_cache_path(),
			show_dialog=False,
		)

	def reset_spotify_auth(self):
		"""Reset Spotify API auth state and retry on next control request."""
		self._spotify_api = None
		self._spotify_oauth_manager = None
		self._spotify_auth_failed = False
		try:
			cache_path = self._spotify_auth_cache_path()
			if os.path.exists(cache_path):
				os.remove(cache_path)
		except Exception:
			pass

	def _mark_spotify_auth_failed(self, exc: Exception):
		"""Mark Spotify auth as failed and avoid retry loops until explicit reset."""
		self._spotify_api = None
		self._spotify_oauth_manager = None
		if not self._spotify_auth_failed:
			print(f"   [DEBUG] Spotify auth/server error: {exc}")
			print(f"   [DEBUG] Redirect URI: {self._spotify_redirect_uri()}")
			print("   [DEBUG] Run reset_spotify_auth() after fixing config to retry.")
		self._spotify_auth_failed = True

	def _get_spotify_api(self) -> spotipy.Spotify | None:
		"""Return a Spotify API client using standard Spotipy OAuth flow."""
		if self._spotify_api is not None:
			return self._spotify_api
		if self._spotify_auth_failed:
			return None

		with self._spotify_auth_lock:
			if self._spotify_api is not None:
				return self._spotify_api

			try:
				auth_manager = self._spotify_oauth_manager or self._build_spotify_oauth_manager()
				self._spotify_oauth_manager = auth_manager
				self._spotify_api = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=10, retries=2)
				# Triggers OAuth only when needed and verifies token is valid.
				self._spotify_api.current_user()
				self._spotify_auth_failed = False
				return self._spotify_api
			except Exception as exc:
				self._mark_spotify_auth_failed(exc)
				return None

	def _pick_spotify_device_id(self, sp: spotipy.Spotify) -> str | None:
		"""Pick an active or likely desktop-capable Spotify Connect device."""
		if self._spotify_device_id:
			return self._spotify_device_id

		try:
			devices_resp = sp.devices() or {}
			devices = devices_resp.get("devices", []) or []
			if not devices:
				return None

			active = next((d for d in devices if d.get("is_active")), None)
			if active and active.get("id"):
				self._spotify_device_id = active["id"]
				return self._spotify_device_id

			desktop = next((d for d in devices if d.get("type") == "Computer" and d.get("id")), None)
			if desktop:
				self._spotify_device_id = desktop["id"]
				return self._spotify_device_id

			first = next((d for d in devices if d.get("id")), None)
			if first:
				self._spotify_device_id = first["id"]
				return self._spotify_device_id
		except Exception as exc:
			print(f"   [DEBUG] Failed to list Spotify devices: {exc}")

		return None

	def _ensure_spotify_device(self, sp: spotipy.Spotify) -> str | None:
		"""Try to transfer playback to an available Spotify device."""
		device_id = self._pick_spotify_device_id(sp)
		if not device_id:
			return None

		try:
			sp.transfer_playback(device_id=device_id, force_play=False)
		except SpotifyException as exc:
			# Device might already be active or temporarily unavailable.
			print(f"   [DEBUG] Spotify transfer playback warning: {exc}")
		except Exception as exc:
			self._mark_spotify_auth_failed(exc)
		return device_id

	def _spotify_start_track(self, track: Track) -> bool:
		"""Play a specific track via Spotify Web API."""
		sp = self._get_spotify_api()
		if sp is None:
			return False

		uri = self._spotify_uri_for_track(track)
		if not uri:
			return False

		device_id = self._ensure_spotify_device(sp)
		try:
			if device_id:
				sp.start_playback(device_id=device_id, uris=[uri])
			else:
				sp.start_playback(uris=[uri])
			return True
		except SpotifyException as exc:
			print(f"   [DEBUG] Spotify API start_playback failed: {exc}")
			return False
		except Exception as exc:
			self._mark_spotify_auth_failed(exc)
			return False

	def _spotify_pause(self) -> bool:
		"""Pause Spotify playback via API."""
		sp = self._get_spotify_api()
		if sp is None:
			return False

		device_id = self._pick_spotify_device_id(sp)
		try:
			if device_id:
				sp.pause_playback(device_id=device_id)
			else:
				sp.pause_playback()
			return True
		except SpotifyException as exc:
			print(f"   [DEBUG] Spotify API pause failed: {exc}")
			return False
		except Exception as exc:
			self._mark_spotify_auth_failed(exc)
			return False

	def _spotify_toggle_playback(self) -> bool:
		"""Toggle Spotify playback via API (pause if playing, otherwise resume)."""
		sp = self._get_spotify_api()
		if sp is None:
			return False

		device_id = self._pick_spotify_device_id(sp)
		try:
			playback = sp.current_playback() or {}
			if playback.get("is_playing"):
				if device_id:
					sp.pause_playback(device_id=device_id)
				else:
					sp.pause_playback()
			else:
				if device_id:
					sp.start_playback(device_id=device_id)
				else:
					sp.start_playback()
			return True
		except SpotifyException as exc:
			print(f"   [DEBUG] Spotify API play/pause toggle failed: {exc}")
			return False
		except Exception as exc:
			self._mark_spotify_auth_failed(exc)
			return False

	def _spotify_next_track(self) -> bool:
		"""Skip to next Spotify track via API."""
		sp = self._get_spotify_api()
		if sp is None:
			return False

		device_id = self._pick_spotify_device_id(sp)
		try:
			if device_id:
				sp.next_track(device_id=device_id)
			else:
				sp.next_track()
			return True
		except SpotifyException as exc:
			print(f"   [DEBUG] Spotify API next_track failed: {exc}")
			return False
		except Exception as exc:
			self._mark_spotify_auth_failed(exc)
			return False

	def _spotify_previous_track(self) -> bool:
		"""Go to previous Spotify track via API."""
		sp = self._get_spotify_api()
		if sp is None:
			return False

		device_id = self._pick_spotify_device_id(sp)
		try:
			if device_id:
				sp.previous_track(device_id=device_id)
			else:
				sp.previous_track()
			return True
		except SpotifyException as exc:
			print(f"   [DEBUG] Spotify API previous_track failed: {exc}")
			return False
		except Exception as exc:
			self._mark_spotify_auth_failed(exc)
			return False

	def play_track(self, track: Track):
		"""Play YouTube in browser and Spotify via API (desktop app target)."""
		if track.platform == "youtube" and (self._spotify_playing or self.current_platform == "spotify"):
			paused = self._spotify_pause()
			if not paused:
				print("   [DEBUG] Failed to stop Spotify via API before switching to YouTube.")
				return
			self._spotify_playing = False
			self.current_platform = None

		if track.platform != "spotify":
			return super().play_track(track)

		print("\n▶️  Now Playing:")
		print(f"   Platform: {track.platform.upper()}")
		print(f"   Title: {track.title}")
		print(f"   Artist: {track.artist}")

		# Ensure browser YouTube tab is closed before switching to Spotify desktop playback.
		if self._youtube_playing and self._current_track_title:
			self._close_browser_tab(self._current_track_title)

		self._youtube_playing = False
		self._spotify_playing = True
		self.current_platform = "spotify"
		self._current_track_title = track.title

		api_ok = self._spotify_start_track(track)
		if api_ok:
			print("   → Started via Spotify Web API")
			time.sleep(0.8)
			self._focus_spotify_app()
		else:
			print("   [DEBUG] Spotify API play failed. No fallback is enabled.")
			return

		if track.duration:
			timer_dur = track.duration - 4 if track.duration > 4 else track.duration
			if timer_dur < 1:
				timer_dur = max(0.5, track.duration * 0.9)
			self._start_autoplay_timer(timer_dur)

		self.played_tracks.append(track)
		key = self._track_key(track)
		self.play_counts[key] = self.play_counts.get(key, 0) + 1
		print(f"   [DEBUG] Play count for '{key}': {self.play_counts[key]}")
		self._save_play_counts()

	def stop_current(self, wait_after: bool = True):
		"""Stop current track: close browser tab for YouTube, pause app for Spotify."""
		if self._spotify_playing or self.current_platform == "spotify":
			self._clear_autoplay_bookkeeping()
			paused = self._spotify_pause()
			if not paused:
				print("   [DEBUG] Spotify API stop failed. No fallback is enabled.")
				return

			self._youtube_playing = False
			self._spotify_playing = False
			self._current_track_title = None
			self.current_platform = None

			if wait_after:
				time.sleep(0.2)
			return

		return super().stop_current(wait_after=wait_after)

	def pause_playback(self) -> bool:
		"""Pause/unpause playback with Spotify API for Spotify tracks."""
		if self._spotify_playing or (self.current_platform == "spotify"):
			if self._autoplay_timer and not self._autoplay_paused:
				if self._autoplay_start_time and self._autoplay_duration:
					elapsed = time.time() - self._autoplay_start_time
					remaining = self._autoplay_duration - elapsed
				else:
					remaining = None
				self._autoplay_timer.cancel()
				self._autoplay_timer = None
				self._autoplay_remaining = None if remaining is None else max(0.1, float(remaining))
				self._autoplay_paused = True
			elif self._autoplay_paused and (self._autoplay_remaining is not None):
				self._start_autoplay_timer(self._autoplay_remaining)
				self._autoplay_remaining = None
				self._autoplay_paused = False

			toggled = self._spotify_toggle_playback()
			if not toggled:
				print("   [DEBUG] Spotify API pause/play failed. No fallback is enabled.")
				return False
			return True

		return super().pause_playback()

	def next_spotify_track(self) -> bool:
		"""Skip to next Spotify track and re-arm timer if needed."""
		ok = self._spotify_next_track()
		if ok and self.played_tracks:
			last = self.played_tracks[-1]
			if last.platform == "spotify" and last.duration:
				timer_dur = last.duration - 4 if last.duration > 4 else last.duration
				if timer_dur < 1:
					timer_dur = max(0.5, last.duration * 0.9)
				self._start_autoplay_timer(timer_dur)
		return ok

	def previous_spotify_track(self) -> bool:
		"""Go to previous Spotify track via API."""
		return self._spotify_previous_track()
