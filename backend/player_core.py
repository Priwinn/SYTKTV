"""Backend player core and playback/session logic."""

import json
import os
import random
import subprocess
import sys
import threading
import time
import webbrowser
import ctypes
from typing import Optional

import pyautogui
import pygetwindow as gw

from backend.services.spotify_playlist import SpotifyPlaylist
from backend.services.youtube_playlist import YouTubePlaylist
from models import Track


class RandomPlayer:
	"""Main player that randomly selects and plays content."""

	def __init__(self):
		self.youtube_tracks: list[Track] = []
		self.spotify_tracks: list[Track] = []
		self.all_tracks: list[Track] = []
		self.played_tracks: list[Track] = []
		self.current_browser_process: Optional[subprocess.Popen] = None
		self.current_platform: Optional[str] = None

		# Playlist refresh settings
		self._refresh_thread: Optional[threading.Thread] = None
		self._stop_refresh = threading.Event()
		self._playlist_lock = threading.Lock()
		self._youtube_url: str = ""
		self._spotify_url: str = ""
		self._spotify_client_id: str = ""
		self._spotify_client_secret: str = ""
		self.refresh_interval: int = 10  # seconds
		self._youtube_playing: bool = False
		self._spotify_playing: bool = False
		self._current_track_title: Optional[str] = None
		# play counts keyed by track identifier
		self.play_counts: dict[str, int] = {}
		# autoplay timer
		self._autoplay_timer: Optional[threading.Timer] = None
		# Autoplay timer bookkeeping for pause/resume
		self._autoplay_start_time: Optional[float] = None
		self._autoplay_duration: Optional[float] = None
		self._autoplay_remaining: Optional[float] = None
		self._autoplay_paused: bool = False
		# queue for upcoming tracks
		self._queue: list[Track] = []
		# Next Up window handle (optional)
		self._next_up_window = None
		# Whether to show who added tracks in the Next Up window
		self._show_adder_nextup = False
		# Optional Demucs live mix slider integration for the main menu UI.
		self._demucs_mix_controller = None
		self._demucs_mix_slider_ui = None
		self._demucs_mix_prewarm_started = False
		self._demucs_mix_prewarm_lock = threading.Lock()
		self._demucs_live_thread: Optional[threading.Thread] = None
		self._demucs_live_harness = None
		self._demucs_live_lock = threading.Lock()
		# VR calibration points (can be updated via GUI)
		# Structure:
		#  'base': [(x1,y1),(x2,y2)]
		#  'spotify_last': (x,y)
		#  'youtube_last': (x,y)
		#  'youtube_extra': (x,y)  # extra click for YouTube
		self._vr_points = {
			"base": [(1694, 69), (1640, 127)],
			"spotify_last": (1640, 590),
			"youtube_last": (1640, 640),
			"youtube_extra": (1643, 20),
		}
		# Load persisted play counts from disk if present
		self._load_play_counts()
		# Load persisted VR calibration if present
		self._load_vr_points()

	def _project_root(self) -> str:
		return os.path.dirname(os.path.dirname(__file__))

	def _play_counts_path(self) -> str:
		return os.path.join(self._project_root(), "play_counts.json")

	def _load_play_counts(self):
		"""Load play counts from JSON file into self.play_counts."""
		path = self._play_counts_path()
		if not os.path.exists(path):
			return
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data, dict):
			return

		for k, v in data.items():
			try:
				self.play_counts[str(k)] = int(v)
			except (TypeError, ValueError):
				self.play_counts[str(k)] = 0

	def _save_play_counts(self):
		"""Atomically save play_counts to JSON on disk."""
		path = self._play_counts_path()
		tmp = path + ".tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(self.play_counts, f, indent=2, ensure_ascii=False)
		os.replace(tmp, path)

	def _vr_points_path(self) -> str:
		return os.path.join(self._project_root(), "vr_calibration.json")

	def _load_vr_points(self):
		"""Load VR calibration points from JSON file into self._vr_points."""
		path = self._vr_points_path()
		if not os.path.exists(path):
			return
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
		if not isinstance(data, dict):
			return
		base = data.get("base")
		if isinstance(base, list) and len(base) >= 2:
			if (
				isinstance(base[0], (list, tuple))
				and isinstance(base[1], (list, tuple))
				and len(base[0]) >= 2
				and len(base[1]) >= 2
			):
				self._vr_points["base"] = [
					(int(base[0][0]), int(base[0][1])),
					(int(base[1][0]), int(base[1][1])),
				]
		for key in ("spotify_last", "youtube_last", "youtube_extra"):
			v = data.get(key)
			if isinstance(v, (list, tuple)) and len(v) >= 2:
				self._vr_points[key] = (int(v[0]), int(v[1]))

	def _save_vr_points(self):
		"""Atomically save vr calibration to JSON on disk."""
		path = self._vr_points_path()
		tmp = path + ".tmp"
		out = {}
		b = self._vr_points.get("base", [])
		if isinstance(b, (list, tuple)) and len(b) >= 2:
			out["base"] = [[int(b[0][0]), int(b[0][1])], [int(b[1][0]), int(b[1][1])]]
		else:
			out["base"] = []
		for key in ("spotify_last", "youtube_last", "youtube_extra"):
			v = self._vr_points.get(key)
			if isinstance(v, (list, tuple)) and len(v) >= 2:
				out[key] = [int(v[0]), int(v[1])]
		with open(tmp, "w", encoding="utf-8") as f:
			json.dump(out, f, indent=2, ensure_ascii=False)
		os.replace(tmp, path)

	def get_demucs_mix_controller(self):
		"""Return a shared StemMixController instance for live voice-mix UI."""
		if self._demucs_mix_controller is not None:
			return self._demucs_mix_controller

		from vr.rt_audio_harness_demucs import StemMixController

		self._demucs_mix_controller = StemMixController(vocal_mix=0.5, mode="add-vocals")
		return self._demucs_mix_controller

	def open_demucs_mix_slider(self) -> bool:
		"""Open the Demucs vocal/instrumental slider window from the main GUI."""
		try:
			# Lazy-start live processing only when Voice Mix is opened.
			self.start_demucs_live_processing()
			controller = self.get_demucs_mix_controller()
			if self._demucs_mix_slider_ui is None:
				from vr.rt_audio_harness_demucs import MixSliderUI

				self._demucs_mix_slider_ui = MixSliderUI(controller)
			return bool(self._demucs_mix_slider_ui.start())
		except Exception as exc:
			print(f"Failed to open Demucs mix slider: {exc}")
			return False

	def prewarm_demucs_mix_controller(self):
		"""Initialize Demucs mix controller/UI in a background thread once."""
		with self._demucs_mix_prewarm_lock:
			if self._demucs_mix_prewarm_started:
				return
			self._demucs_mix_prewarm_started = True

		def _prewarm():
			try:
				controller = self.get_demucs_mix_controller()
				if self._demucs_mix_slider_ui is None:
					from vr.rt_audio_harness_demucs import MixSliderUI

					self._demucs_mix_slider_ui = MixSliderUI(controller)
			except Exception as exc:
				print(f"Demucs mix prewarm failed: {exc}")

		thread = threading.Thread(target=_prewarm, daemon=True)
		thread.start()

	def _build_demucs_live_harness(self):
		"""Create a configured LiveLoopbackHarness connected to shared mix controller."""
		if sys.platform != "win32":
			raise RuntimeError("Live Demucs harness currently targets Windows WASAPI.")

		from vr import rt_audio_harness_demucs as demucs_live

		if "Windows WASAPI" not in [api["name"] for api in demucs_live.sd.query_hostapis()]:
			raise RuntimeError("Windows WASAPI host API was not found.")

		output_index = demucs_live.get_system_default_output_index()
		preferred_input_host = demucs_live.hostapi_name_for_device(output_index)
		input_index = demucs_live.find_vb_cable_input(preferred_host_name=preferred_input_host)
		if input_index is None:
			input_index = demucs_live.find_wasapi_loopback_input(output_index)
		if input_index is None:
			raise RuntimeError("No VB-CABLE input or WASAPI loopback input found.")

		devices = demucs_live.sd.query_devices()
		print(
			f"[demucs-live] input [{input_index}]: {devices[input_index]['name']} "
			f"({demucs_live.hostapi_name_for_device(input_index)})"
		)
		print(
			f"[demucs-live] output [{output_index}]: {devices[output_index]['name']} "
			f"({demucs_live.hostapi_name_for_device(output_index)})"
		)

		separator = demucs_live.create_separator(
			"demucs-vocals-inst",
			demucs_model="htdemucs",
			demucs_device="cuda",
			demucs_segment_sec=demucs_live.parse_demucs_segment_arg("1"),
			demucs_overlap=0.0,
			demucs_loudness_match=True,
		)

		return demucs_live.LiveLoopbackHarness(
			samplerate=48000,
			channels=2,
			blocksize=48000,
			latency_buffers=8,
			gain=1.0,
			input_device=input_index,
			output_device=output_index,
			separator=separator,
			mix_controller=self.get_demucs_mix_controller(),
			bypass_high_watermark=6,
			bypass_low_watermark=3,
			bypass_enabled=True,
		)

	def start_demucs_live_processing(self):
		"""Start the live Demucs processing harness on a background thread."""
		with self._demucs_live_lock:
			if self._demucs_live_thread and self._demucs_live_thread.is_alive():
				return

		def _runner():
			com_initialized = False
			ole32 = None
			try:
				# WASAPI stream setup can fail in worker threads if COM is not initialized.
				if os.name == "nt":
					try:
						ole32 = ctypes.windll.ole32
						hr = int(ole32.CoInitializeEx(None, 0x0))  # COINIT_MULTITHREADED
						if hr in (0, 1):  # S_OK / S_FALSE
							com_initialized = True
					except Exception as exc:
						print(f"[demucs-live] COM init warning: {exc}")

				harness = self._build_demucs_live_harness()
				with self._demucs_live_lock:
					self._demucs_live_harness = harness
				print("[demucs-live] started")
				harness.run(duration=0.0, verbose_interval=2.0)
			except Exception as exc:
				print(f"[demucs-live] failed: {exc}")
			finally:
				if com_initialized and ole32 is not None:
					try:
						ole32.CoUninitialize()
					except Exception:
						pass
				with self._demucs_live_lock:
					self._demucs_live_harness = None
					self._demucs_live_thread = None

		thread = threading.Thread(target=_runner, daemon=True)
		self._demucs_live_thread = thread
		thread.start()

	def stop_demucs_live_processing(self):
		"""Stop the live Demucs processing harness if it's running."""
		with self._demucs_live_lock:
			harness = self._demucs_live_harness
			thread = self._demucs_live_thread

		if harness is not None:
			harness.stop_event.set()

		if thread is not None and thread.is_alive():
			thread.join(timeout=2.0)

	def _focus_tab_by_title(self, search_title: str) -> Optional[gw.Window]:
		"""Find and focus a browser tab containing the track title."""

		pyautogui.FAILSAFE = False

		# Get first few words of title to match (more reliable)
		# Split title and take first 3 words or first 20 chars
		search_words = search_title.lower().split()[:3]
		search_partial = " ".join(search_words) if search_words else search_title[:20].lower()

		all_windows = gw.getAllWindows()

		# If not found directly, search through browser tabs
		browser_windows = []
		for win in all_windows:
			if not win.title or not win.title.strip():
				continue
			title_lower = win.title.lower()
			if any(b in title_lower for b in ["chrome", "edge", "firefox", "brave", "opera"]):
				browser_windows.append(win)

		for browser_win in browser_windows:
			browser_win.activate()
			time.sleep(0.3)

			original_title = browser_win.title
			max_tabs = 15

			for i in range(max_tabs):
				current_win = gw.getActiveWindow()
				if current_win and current_win.title:
					title_lower = current_win.title.lower()
					if search_partial in title_lower or any(word in title_lower for word in search_words if len(word) > 3):
						return gw.getActiveWindow()

				pyautogui.hotkey("ctrl", "tab")
				time.sleep(0.2)

				new_win = gw.getActiveWindow()
				if new_win and new_win.title == original_title and i > 0:
					break

		return None

	def _close_browser_tab(self, search_title: str) -> bool:
		"""Find and close a browser tab containing the track title."""
		pyautogui.FAILSAFE = False

		focused = self._focus_tab_by_title(search_title)
		if not focused:
			return False

		pyautogui.hotkey("ctrl", "w")
		time.sleep(0.2)
		return True

	def _navigate_in_same_tab(self, search_title: str, new_url: str) -> bool:
		"""Find a browser tab containing search_title, activate it, press F11,
		focus the address bar, type `new_url` and press Enter. Returns True
		if navigation was attempted on an existing tab, False otherwise.
		"""
		pyautogui.FAILSAFE = False

		# Try to focus the tab via helper
		focused = self._focus_tab_by_title(search_title)

		if not focused:
			return False

		# Attempt fullscreen/address bar navigation on the focused tab
		if focused is not None:
			if self.is_window_fullscreen(focused):
				pyautogui.press("f11")
				time.sleep(0.2)
				pyautogui.press("f")
				time.sleep(0.2)
				pyautogui.press("f")
				time.sleep(0.2)

		time.sleep(0.2)
		pyautogui.hotkey("ctrl", "l")
		pyautogui.hotkey("alt", "d")
		time.sleep(0.1)
		pyautogui.typewrite(new_url, interval=0.01)
		pyautogui.press("enter")
		time.sleep(0.5)
		pyautogui.press("f11")

		return True

	def _focus_spotify_app(self):
		"""Find and focus the Spotify desktop app by process name."""
		import ctypes
		from ctypes import wintypes

		# Windows API functions
		user32 = ctypes.windll.user32
		kernel32 = ctypes.windll.kernel32
		psapi = ctypes.windll.psapi

		# Get window handle by enumerating all windows and checking process name
		EnumWindows = user32.EnumWindows
		EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
		GetWindowThreadProcessId = user32.GetWindowThreadProcessId
		OpenProcess = kernel32.OpenProcess
		CloseHandle = kernel32.CloseHandle
		GetModuleBaseNameW = psapi.GetModuleBaseNameW
		IsWindowVisible = user32.IsWindowVisible
		SetForegroundWindow = user32.SetForegroundWindow
		ShowWindow = user32.ShowWindow

		PROCESS_QUERY_INFORMATION = 0x0400
		PROCESS_VM_READ = 0x0010
		SW_SHOW = 5

		spotify_hwnd = None

		def enum_callback(hwnd, lParam):
			nonlocal spotify_hwnd
			if not IsWindowVisible(hwnd):
				return True

			# Get process ID for this window
			pid = wintypes.DWORD()
			GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

			# Open process to get its name
			hProcess = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value)
			if hProcess:
				try:
					buffer = ctypes.create_unicode_buffer(260)
					if GetModuleBaseNameW(hProcess, None, buffer, 260) > 0:
						process_name = buffer.value.lower()
						if process_name == "spotify.exe":
							spotify_hwnd = hwnd
							return False  # Stop enumeration
				finally:
					CloseHandle(hProcess)
			return True

		EnumWindows(EnumWindowsProc(enum_callback), 0)

		if spotify_hwnd:
			ShowWindow(spotify_hwnd, SW_SHOW)
			time.sleep(0.1)
			SetForegroundWindow(spotify_hwnd)

	def is_window_fullscreen(self, win, tol: int = 2) -> bool:
		"""Return True if the given pygetwindow Window appears fullscreen.

		Checks the native window rect against its monitor rect (multi-monitor
		aware). If HWND is not available or Win32 calls fail, falls back to
		comparing the window size to the primary screen size with a small
		tolerance.
		"""
		try:
			if not win:
				return False

			# Try to get native HWND from pygetwindow Window
			hwnd = getattr(win, "_hWnd", None)
			if hwnd:
					import ctypes
					from ctypes import wintypes

					MONITOR_DEFAULTTONEAREST = 2

					class MONITORINFO(ctypes.Structure):
						_fields_ = (
							("cbSize", wintypes.DWORD),
							("rcMonitor", wintypes.RECT),
							("rcWork", wintypes.RECT),
							("dwFlags", wintypes.DWORD),
						)

					user32 = ctypes.windll.user32

					rect = wintypes.RECT()
					if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
						hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
						mi = MONITORINFO()
						mi.cbSize = ctypes.sizeof(mi)
						if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
							wr, mr = rect, mi.rcMonitor
							# Compare window rect to monitor rect with tolerance
							return (
								abs(wr.left - mr.left) <= tol
								and abs(wr.top - mr.top) <= tol
								and abs(wr.right - mr.right) <= tol
								and abs(wr.bottom - mr.bottom) <= tol
							)

			# Fallback: compare window size to primary screen size
			screen_w, screen_h = pyautogui.size()
			w_w = getattr(win, "width", 0)
			w_h = getattr(win, "height", 0)
			return w_w >= screen_w - tol and w_h >= screen_h - tol
		except Exception:
			return False

	def perform_vr_reset(self):
		"""Bring a browser window to the foreground and click a predefined sequence of points that resets Voice Removal."""
		pyautogui.FAILSAFE = False

		# Try to find a browser window by common browser names
		browser_keywords = ["chrome", "firefox", "edge", "brave", "opera"]
		all_windows = gw.getAllWindows()
		target_win = None
		for w in all_windows:
			if w.title and any(b in w.title.lower() for b in browser_keywords):
				target_win = w
				break

		# Fallback: pick the first top-level window
		if not target_win and all_windows:
			target_win = all_windows[0]

		if target_win:
			target_win.activate()
			time.sleep(0.3)

		# Detect platform playing state and toggle fullscreen for YouTube
		is_youtube = self.current_platform == "youtube"
		is_spotify = self.current_platform == "spotify"

		# Deactivate fullscreen if needed
		if self.is_window_fullscreen(target_win):
			pyautogui.press("f11")
			time.sleep(0.2)
			pyautogui.press("f")
			time.sleep(0.2)
			pyautogui.press("f")
			time.sleep(0.2)

		# Build the points list using calibration if available.
		base = self._vr_points.get("base", [(1694, 69), (1640, 127)])
		if is_youtube:
			last = self._vr_points.get("youtube_last", (1640, 640))
		else:
			last = self._vr_points.get("spotify_last", (1640, 590))

		points = list(base) + [last]
		print(f"   [DEBUG] Performing VR reset (YouTube: {is_youtube}, Spotify: {is_spotify}), points={points}")
		for x, y in points:
			if pyautogui.pixel(x, y) == (76, 255, 0):
				time.sleep(0.1)
				pyautogui.click(x, y)
				print("   [DEBUG] Detected green, turning it off and on")
			time.sleep(0.3)
			pyautogui.click(x, y)
			time.sleep(0.1)
			time.sleep(0.25)

		if is_youtube:
			time.sleep(0.2)
			y_extra = self._vr_points.get("youtube_extra", (1643, 20))
			pyautogui.click(y_extra[0], y_extra[1])
			# Press F11 once more before restoring via 'f'
			pyautogui.press("f11")
			time.sleep(0.2)
			# pyautogui.press('f')

		# If Spotify was playing, press F11 again and bring the desktop Spotify app back to front
		if is_spotify:
			time.sleep(0.2)
			pyautogui.press("f11")
			time.sleep(0.1)
			self._focus_spotify_app()

	def perform_vr_on(self):
		"""Perform VR ON sequence: click first point always; for remaining
		points click only if the pixel at that coordinate is NOT green.
		Returns set of playing pids like perform_vr_reset.
		"""

		pyautogui.FAILSAFE = False

		# Find a browser window to activate
		browser_keywords = ["chrome", "firefox", "edge", "brave", "opera"]
		all_windows = gw.getAllWindows()
		target_win = None
		for w in all_windows:
			if w.title and any(b in w.title.lower() for b in browser_keywords):
				target_win = w
				break

		if not target_win and all_windows:
			target_win = all_windows[0]

		if target_win:
			target_win.activate()
			time.sleep(0.3)

		is_youtube = self.current_platform == "youtube"
		is_spotify = self.current_platform == "spotify"

		# Determine last point based on platform and calibration
		base = self._vr_points.get("base", [(1694, 69), (1640, 127)])
		if is_youtube:
			last = self._vr_points.get("youtube_last", (1640, 640))
		else:
			last = self._vr_points.get("spotify_last", (1640, 590))

		# Deactivate fullscreen if needed
		if self.is_window_fullscreen(target_win):
			pyautogui.press("f11")
			time.sleep(0.2)
			pyautogui.press("f")
			time.sleep(0.2)
			pyautogui.press("f")
			time.sleep(0.2)

		# Points to click (first is always clicked)
		points = list(base) + [last]

		for idx, (x, y) in enumerate(points):
			if idx == 0:
				# Always click first point
				pyautogui.click(x, y)
			else:
				pix = pyautogui.pixel(x, y)
				# If pixel is green (76,255,0) skip click
				if pix is None or tuple(pix) != (76, 255, 0):
					pyautogui.click(x, y)
			time.sleep(0.25)
			time.sleep(0.25)

		if is_youtube:
			time.sleep(0.2)
			y_extra = self._vr_points.get("youtube_extra", (1643, 20))
			pyautogui.click(y_extra[0], y_extra[1])
			# Press F11 then restore via 'f'
			pyautogui.press("f11")
			time.sleep(0.2)
			pyautogui.press("f")

		if is_spotify:
			time.sleep(0.2)
			pyautogui.press("f11")
			time.sleep(0.1)
			self._focus_spotify_app()

	def perform_vr_off(self):
		"""Perform VR OFF sequence: click the first point always; check the
		last point and click it only if it is GREEN (indicates ON), then
		perform the same pre/post F/F11 handling as the other VR routines.
		Returns set of playing pids like perform_vr_on.
		"""
		pyautogui.FAILSAFE = False

		# Find a browser window to activate
		browser_keywords = ["chrome", "firefox", "edge", "brave", "opera"]
		all_windows = gw.getAllWindows()
		target_win = None
		for w in all_windows:
			if w.title and any(b in w.title.lower() for b in browser_keywords):
				target_win = w
				break

		if not target_win and all_windows:
			target_win = all_windows[0]

		if target_win:
			target_win.activate()
			time.sleep(0.3)

		is_youtube = self.current_platform == "youtube"
		is_spotify = self.current_platform == "spotify"

		# Determine last point based on platform and calibration
		base = self._vr_points.get("base", [(1694, 69), (1640, 127)])
		if is_youtube:
			last = self._vr_points.get("youtube_last", (1640, 640))
		else:
			last = self._vr_points.get("spotify_last", (1640, 590))

		# Deactivate fullscreen if needed
		if self.is_window_fullscreen(target_win):
			pyautogui.press("f11")
			time.sleep(0.2)
			pyautogui.press("f")
			time.sleep(0.2)
			pyautogui.press("f")
			time.sleep(0.2)

		first = tuple(base[0]) if base and len(base) > 0 else (1694, 69)
		# last is already set above

		# Always click first point
		pyautogui.click(first[0], first[1])
		time.sleep(0.3)

		# Check last pixel; click it only if it's green (76,255,0)
		pix = pyautogui.pixel(last[0], last[1])
		if pix is not None and tuple(pix) == (76, 255, 0):
			pyautogui.click(last[0], last[1])
			time.sleep(0.3)

		# Post-click state: mirror other routines
		if is_youtube:
			time.sleep(0.2)
			y_extra = self._vr_points.get("youtube_extra", (1643, 20))
			pyautogui.click(y_extra[0], y_extra[1])
			pyautogui.press("f11")
			time.sleep(0.2)
			pyautogui.press("f")

		if is_spotify:
			time.sleep(0.2)
			pyautogui.press("f11")
			time.sleep(0.1)
			self._focus_spotify_app()

	def _track_key(self, track: Track) -> str:
		"""Return a stable key identifying a track for counting plays."""
		if track.uri:
			return track.uri
		if track.url:
			return track.url
		return f"{track.platform}:{track.title} - {track.artist}"

	def _fill_queue(self, platform: Optional[str] = None):
		"""Fill the queue with upcoming random tracks, prioritizing least played."""
		with self._playlist_lock:
			tracks = self.all_tracks if platform is None else (self.youtube_tracks if platform == "youtube" else self.spotify_tracks)
			if not tracks:
				return
			# Get candidates: tracks with lowest play count
			counts = [self.play_counts.get(self._track_key(t), 0) for t in tracks]
			min_count = min(counts) if counts else 0
			candidates = [t for t, c in zip(tracks, counts) if c == min_count]
			if not candidates:
				candidates = tracks
			# Shuffle and add to queue
			random.shuffle(candidates)
			self._queue.extend(candidates)
			print(f"   [DEBUG] Filled queue with {len(self._queue)} tracks ({platform or 'all'})")

	def stop_current(self, wait_after: bool = True):
		"""Stop the currently playing track - closes the tab by searching for the track title."""
		# cancel any autoplay timer
		if self._autoplay_timer:
			self._autoplay_timer.cancel()
			self._autoplay_timer = None
		# clear bookkeeping
		self._autoplay_start_time = None
		self._autoplay_duration = None
		self._autoplay_remaining = None
		self._autoplay_paused = False
		if not self._current_track_title:
			return

		closed = False

		if self._youtube_playing or self._spotify_playing:
			if self._close_browser_tab(self._current_track_title):
				print("   ⏹️  Closed track tab")
				closed = True

		self._youtube_playing = False
		self._spotify_playing = False
		self._current_track_title = None
		self.current_platform = None

		if closed and wait_after:
			time.sleep(0.3)

	def pause_playback(self) -> bool:
		"""Pause/unpause playback by focusing the relevant browser tab or app and pressing Space.

		Returns True if the keypress was attempted, False otherwise.
		"""
		pyautogui.FAILSAFE = False

		# Try to focus the browser tab for the current track title
		if self._current_track_title:
			self._focus_tab_by_title(self._current_track_title)

		# Manage autoplay timer bookkeeping: if a timer is active, pause it
		if self._autoplay_timer and not self._autoplay_paused:
			# compute elapsed and remaining
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
			# resume with remaining time
			self._start_autoplay_timer(self._autoplay_remaining)
			self._autoplay_remaining = None
			self._autoplay_paused = False

		pyautogui.press("space")
		# If playing spotify, focus the app
		if self._spotify_playing or (self.current_platform == "spotify"):
			self._focus_spotify_app()
		return True

	def refresh_current_tab(self) -> bool:
		"""Find the currently playing browser tab (by title) and refresh it.

		Returns True if a refresh keypress was attempted, False otherwise.
		"""
		pyautogui.FAILSAFE = False
		title = self._current_track_title
		if not title:
			return False

		# Try to focus the tab for the current track
		focused = self._focus_tab_by_title(title)

		if not focused:
			return False

		# Attempt Ctrl+R then fall back to F5. If successful, reset autoplay timer
		pyautogui.hotkey("ctrl", "r")

		# Reset autoplay timer based on the currently playing track (last played)
		last = self.played_tracks[-1] if self.played_tracks else None
		if last and getattr(last, "duration", None):
			dur = float(last.duration)
			if last.platform == "spotify":
				# trigger a few seconds early for Spotify like in play_track
				timer_dur = dur - 4 if dur > 4 else dur
				if timer_dur < 1:
					timer_dur = max(0.5, dur * 0.9)
			else:
				timer_dur = dur
			# start a fresh autoplay timer
			self._start_autoplay_timer(timer_dur)

		# give a short moment for the refresh to apply
		time.sleep(0.15)

		# Press F if youTube to restore fullscreen
		if self.current_platform == "youtube":
			time.sleep(2)
			pyautogui.press("f")

		return True

	def _on_track_end(self):
		"""Callback when a track finishes playing."""
		print("\n⏭️  Track finished — auto-playing next track")
		try:
			# clear autoplay bookkeeping (timer already fired)
			if self._autoplay_timer:
				self._autoplay_timer.cancel()
			self._autoplay_timer = None
			self._autoplay_start_time = None
			self._autoplay_duration = None
			self._autoplay_remaining = None
			self._autoplay_paused = False

			# stop current (ensure tabs closed)
			self.stop_current(wait_after=False)
			# play next from queue
			self._play_next_from_queue()

		except Exception as e:
			print(f"   [DEBUG] Error in _on_track_end: {e}")

	def _start_autoplay_timer(self, duration: float):
		"""Start a timer to auto-play next track after duration seconds."""
		try:
			if duration is None or duration <= 0:
				return
			# cancel previous
			if self._autoplay_timer:
				self._autoplay_timer.cancel()
			# reset bookkeeping for a new timer
			self._autoplay_start_time = time.time()
			self._autoplay_duration = float(duration)
			self._autoplay_remaining = None
			self._autoplay_paused = False
			self._autoplay_timer = threading.Timer(duration, self._on_track_end)
			self._autoplay_timer.daemon = True
			self._autoplay_timer.start()
			print(f"   [DEBUG] Autoplay timer started for {duration} seconds")
		except Exception as e:
			print(f"   [DEBUG] Failed to start autoplay timer: {e}")

	def start_menu_window(self):
		"""Start the Next Up (Menu) GUI window in background."""
		if not self._next_up_window:
			from ui.menu_window import Menu
			self._next_up_window = Menu(self)
			self._next_up_window.start()

	def stop_menu_window(self):
		"""Stop the Next Up (Menu) GUI window if running."""
		self.stop_demucs_live_processing()
		if self._next_up_window:
			self._next_up_window.stop()
			self._next_up_window = None

	def toggle_show_adder_menu(self):
		"""Toggle displaying who added tracks in the Next Up (Menu) window."""
		self._show_adder_nextup = not getattr(self, "_show_adder_nextup", False)
		status = "ON" if self._show_adder_nextup else "OFF"
		print(f"Next Up: show adder {status}")
		# Force immediate refresh
		self.update_menu_file()

	def _play_next_from_queue(self):
		"""Play the next track from the queue, refilling if necessary."""
		if not self._queue:
			self._fill_queue()
		if self._queue:
			track = self._queue.pop(0)
			self.play_track(track)
		else:
			print("No tracks in queue!")

	def load_playlists(
		self,
		youtube_url: str,
		spotify_url: str,
		spotify_client_id: str,
		spotify_client_secret: str,
		silent: bool = False,
	):
		"""Load both playlists."""
		# Store URLs for refresh
		self._youtube_url = youtube_url
		self._spotify_url = spotify_url
		self._spotify_client_id = spotify_client_id
		self._spotify_client_secret = spotify_client_secret

		if not silent:
			print("\n🎵 Loading playlists...\n")

		new_youtube_tracks: list[Track] = []
		new_spotify_tracks: list[Track] = []
		tracks = self.all_tracks
		counts = [self.play_counts.get(self._track_key(t), 0) for t in tracks]
		min_count = min(counts) if counts else 0

		# Load YouTube playlist
		if youtube_url:
			yt_playlist = YouTubePlaylist(youtube_url)
			new_youtube_tracks = yt_playlist.fetch_videos() if not silent else self._fetch_youtube_silent(youtube_url)
			new_youtube_tracks = [t for t in new_youtube_tracks if self.play_counts.get(self._track_key(t), 0) == min_count]

		# Load Spotify playlist
		if spotify_url and spotify_client_id and spotify_client_secret:
			sp_playlist = SpotifyPlaylist(spotify_url, spotify_client_id, spotify_client_secret)
			new_spotify_tracks = (
				sp_playlist.fetch_tracks()
				if not silent
				else self._fetch_spotify_silent(spotify_url, spotify_client_id, spotify_client_secret)
			)
			new_spotify_tracks = [t for t in new_spotify_tracks if self.play_counts.get(self._track_key(t), 0) == min_count]

		# Update tracks with lock
		with self._playlist_lock:
			old_all_tracks = self.all_tracks.copy()
			old_count = len(self.all_tracks)
			self.youtube_tracks = new_youtube_tracks
			self.spotify_tracks = new_spotify_tracks
			self.all_tracks = self.youtube_tracks + self.spotify_tracks
			new_count = len(self.all_tracks)

			# Remove tracks no longer in playlists
			removed_tracks = [t for t in self._queue if t.url not in [a.url for a in self.all_tracks]]
			if removed_tracks:
				self._queue = [t for t in self._queue if t.url in [a.url for a in self.all_tracks]]

			# Find new tracks
			new_tracks = [t for t in self.all_tracks if t.url not in [o.url for o in old_all_tracks]]
			if new_tracks:
				# Add new tracks to the queue, avoiding duplicates
				new_unique = [t for t in new_tracks if t.url not in [q.url for q in self._queue]]
				self._queue.extend(new_unique)
			else:
				new_unique = []

		if not silent:
			print(f"\n📊 Total tracks available: {len(self.all_tracks)}")
			print(f"   - YouTube: {len(self.youtube_tracks)}")
			print(f"   - Spotify: {len(self.spotify_tracks)}")
			if new_unique:
				print(f"   - Added {len(new_unique)} new tracks to queue")
			if removed_tracks:
				print(f"   - Removed {len(removed_tracks)} tracks from queue")
		elif new_count != old_count:
			print(f"\n🔄 Playlists refreshed: {new_count} tracks (was {old_count})")
			if new_unique:
				print(f"   - Added {len(new_unique)} new tracks to queue")
			if removed_tracks:
				print(f"   - Removed {len(removed_tracks)} tracks from queue")

		if new_unique or removed_tracks:
			self.update_menu_file()

	def _fetch_youtube_silent(self, url: str) -> list[Track]:
		"""Fetch YouTube playlist silently."""
		import yt_dlp

		ydl_opts = {
			"quiet": True,
			"no_warnings": True,
			"extract_flat": True,
			"skip_download": True,
		}
		videos: list[Track] = []
		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			result = ydl.extract_info(url, download=False)
			if result and "entries" in result:
				for entry in result["entries"]:
					if entry:
						vid = entry.get("id", "")
						duration = entry.get("duration")
						if duration is None and vid:
							info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
							duration = info.get("duration") if info else None
						videos.append(
							Track(
								title=entry.get("title", "Unknown"),
								artist=entry.get("uploader", "Unknown"),
								platform="youtube",
								url=f"https://www.youtube.com/watch?v={vid}",
								duration=float(duration) if duration else None,
							)
						)
		return videos

	def _fetch_spotify_silent(self, url: str, client_id: str, client_secret: str) -> list[Track]:
		"""Fetch Spotify playlist silently."""
		try:
			import re

			import spotipy
			from spotipy.oauth2 import SpotifyClientCredentials

			match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
			if not match:
				return self.spotify_tracks
			playlist_id = match.group(1)

			auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
			sp = spotipy.Spotify(auth_manager=auth_manager)

			tracks: list[Track] = []
			results = sp.playlist_tracks(playlist_id)
			while results:
				for item in results["items"]:
					track = item.get("track")
					if track:
						artists = ", ".join([a["name"] for a in track.get("artists", [])])
						tracks.append(
							Track(
								title=track.get("name", "Unknown"),
								artist=artists or "Unknown",
								platform="spotify",
								url=track.get("external_urls", {}).get("spotify", ""),
								uri=f"spotify:track:{track.get('id', '')}",
								duration=(track.get("duration_ms") / 1000.0) if track.get("duration_ms") else None,
							)
						)
				results = sp.next(results) if results["next"] else None
			return tracks
		except Exception:
			return self.spotify_tracks  # Keep existing on error

	def _refresh_loop(self):
		"""Background thread that refreshes playlists periodically."""
		print("🔄 Refresh thread started")
		while not self._stop_refresh.wait(self.refresh_interval):
			self.load_playlists(
				self._youtube_url,
				self._spotify_url,
				self._spotify_client_id,
				self._spotify_client_secret,
				silent=True,
			)

	def start_auto_refresh(self):
		"""Start the background playlist refresh thread."""
		self._stop_refresh.clear()
		self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
		self._refresh_thread.start()
		print(f"\n🔄 Auto-refresh enabled (every {self.refresh_interval}s)")

	def stop_auto_refresh(self):
		"""Stop the background refresh thread."""
		self._stop_refresh.set()
		if self._refresh_thread:
			self._refresh_thread.join(timeout=1)

	def play_track(self, track: Track):
		"""Play a track based on its platform."""
		# For YouTube, try to reuse the existing browser tab instead of closing it.
		# For Spotify, we handle closing inline to prevent title change issues.
		if track.platform == "youtube":
			reused = False
			if self._current_track_title:
				reused = self._navigate_in_same_tab(self._current_track_title, track.url)

		print("\n▶️  Now Playing:")
		print(f"   Platform: {track.platform.upper()}")
		print(f"   Title: {track.title}")
		print(f"   Artist: {track.artist}")

		if track.platform == "youtube":
			# Store track title for later tab identification
			self._current_track_title = track.title
			# If we successfully reused an existing tab, flags are set by the helper
			if not locals().get("reused", False):
				# Open YouTube video in browser
				webbrowser.open(track.url)
				self.current_platform = "youtube"
				self._youtube_playing = True
				print(f"   → Opened in browser: {track.url}")
				# If this is the first played track overall, press F11 after opening
				pyautogui.FAILSAFE = False
				if len(self.played_tracks) == 0:
					time.sleep(0.5)
					pyautogui.press("f11")

				# Wait for video to load, then press 'f' for YouTube fullscreen toggle
				time.sleep(5)
				pyautogui.press("f")
				print("   → Fullscreen activated")
				# start autoplay timer if duration known
				if track.duration:
					self._start_autoplay_timer(track.duration)
			else:
				# Reused existing tab — set playing flags
				self.current_platform = "youtube"
				self._youtube_playing = True
				print(f"   → Reused existing browser tab for: {track.url}")
				# Wait for video to load, then press 'f' for YouTube fullscreen toggle
				time.sleep(5)
				pyautogui.press("f")
				print("   → Fullscreen activated (reused tab)")
				# start autoplay timer if duration known
				if track.duration:
					self._start_autoplay_timer(track.duration)

		elif track.platform == "spotify":
			# Try to reuse the existing browser tab (YouTube or Spotify) before closing
			reused = False
			if self._current_track_title:
				reused = self._navigate_in_same_tab(self._current_track_title, track.url)

			if reused:
				# Reused tab for Spotify URL
				self._current_track_title = track.title
				self.current_platform = "spotify"
				self._spotify_playing = True
				print(f"   → Reused existing browser tab for: {track.url}")
				# Give page some time to load and then focus desktop Spotify app if desired
				time.sleep(3.5)
				self._focus_spotify_app()
				# start autoplay timer if duration known — trigger 3s early for Spotify
				if track.duration:
					timer_dur = track.duration - 5 if track.duration > 5 else track.duration
					if timer_dur < 1:
						timer_dur = max(0.5, track.duration * 0.9)
					self._start_autoplay_timer(timer_dur)
			else:
				# Close previous YouTube tab if there was one
				if self._youtube_playing and self._current_track_title:
					print(f"   [DEBUG] Closing YouTube tab: '{self._current_track_title}'")
					self._close_browser_tab(self._current_track_title)
					self._youtube_playing = False

				# Close previous Spotify tab first (before it changes title)
				if self._spotify_playing and self._current_track_title:
					print(f"   [DEBUG] Closing Spotify tab: '{self._current_track_title}'")
					result = self._close_browser_tab(self._current_track_title)
					print(f"   [DEBUG] Close result: {result}")
					self._spotify_playing = False
					time.sleep(0.5)  # Wait for tab to close before opening new one
				else:
					print(
						f"   [DEBUG] No Spotify tab to close. "
						f"Playing={self._spotify_playing}, Title={self._current_track_title}"
					)

				# Now update title and open new tab
				self._current_track_title = track.title

				# Open Spotify in browser, then focus the desktop app
				if track.url:
					webbrowser.open(track.url)
					self.current_platform = "spotify"
					self._spotify_playing = True
					print(f"   → Opened in browser: {track.url}")
					# If this is the first played track overall, press F11 after opening
					pyautogui.FAILSAFE = False
					if len(self.played_tracks) == 0:
						time.sleep(0.5)
						pyautogui.press("f11")
						print("   → F11 pressed (first track)")

					# Focus the Spotify desktop app by finding its process
					time.sleep(3)
					self._focus_spotify_app()
					# start autoplay timer if duration known — trigger 3s early for Spotify
					if track.duration:
						timer_dur = track.duration - 4 if track.duration > 4 else track.duration
						# ensure at least 1 second
						if timer_dur < 1:
							timer_dur = max(0.5, track.duration * 0.9)
						self._start_autoplay_timer(timer_dur)

		self.played_tracks.append(track)

		# increment play count
		key = self._track_key(track)
		self.play_counts[key] = self.play_counts.get(key, 0) + 1
		print(f"   [DEBUG] Play count for '{key}': {self.play_counts[key]}")
		# persist updated counts
		self._save_play_counts()

	def play_random(self) -> Optional[Track]:
		"""Play the next track from the queue (randomly filled)."""
		self._play_next_from_queue()
		return None  # Since we don't return the track here

	def play_random_from_platform(self, platform: str) -> Optional[Track]:
		"""Play the next track from the queue, refilling with platform-specific if needed."""
		if not self._queue:
			self._fill_queue(platform)
		self._play_next_from_queue()
		return None

	def update_menu_file(self):
		"""Update the Next Up (Menu) display (GUI). This replaces the previous next_up.txt file.

		If a GUI window is running, schedule an update; otherwise do nothing.
		"""
		if not self._queue:
			return
		if self._next_up_window:
			with self._playlist_lock:
				q_copy = list(self._queue)
				self._next_up_window.schedule_update(q_copy)
