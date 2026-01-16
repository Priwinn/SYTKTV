"""
Random Playlist Player - Plays content from YouTube or Spotify playlists randomly
"""

import os
import re
import random
import json
import webbrowser
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv
import qrcode
import io
from contextlib import redirect_stdout
import pyfiglet
import pyautogui
import pygetwindow as gw

# Load environment variables
load_dotenv()


def get_qr_lines(url: str) -> list[str]:
    """Generate QR code ASCII lines for a URL."""
    qr = qrcode.QRCode(version=1, box_size=1, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    f = io.StringIO()
    with redirect_stdout(f):
        qr.print_ascii()
    ascii_str = f.getvalue()
    lines = ascii_str.split('\n')
    return lines


@dataclass
class Track:
    """Represents a track from either platform"""
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


class YouTubePlaylist:
    """Handles YouTube playlist extraction"""
    
    def __init__(self, playlist_url: str):
        self.playlist_url = playlist_url
        self.videos: list[Track] = []
        
    def extract_playlist_id(self) -> Optional[str]:
        """Extract playlist ID from URL"""
        patterns = [
            r'list=([a-zA-Z0-9_-]+)',
            r'playlist\?list=([a-zA-Z0-9_-]+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, self.playlist_url)
            if match:
                return match.group(1)
        return None
    
    def fetch_videos(self) -> list[Track]:
        """Fetch videos from the playlist using yt-dlp"""
        try:
            import yt_dlp
            
            playlist_id = self.extract_playlist_id()
            if not playlist_id:
                print("Error: Could not extract playlist ID from URL")
                return []
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'skip_download': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(self.playlist_url, download=False)
                
                if result and 'entries' in result:
                    for entry in result['entries']:
                        if entry:
                            video_id = entry.get('id', '')
                            title = entry.get('title', 'Unknown Title')
                            uploader = entry.get('uploader', 'Unknown Artist')
                            duration = entry.get('duration')
                            # if duration is not present, try to fetch video info
                            if duration is None and video_id:
                                try:
                                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                                    duration = info.get('duration')
                                except Exception:
                                    duration = None

                            self.videos.append(Track(
                                title=title,
                                artist=uploader,
                                platform='youtube',
                                url=f'https://www.youtube.com/watch?v={video_id}',
                                duration=float(duration) if duration else None
                            ))
                            
            print(f"✓ Loaded {len(self.videos)} videos from YouTube playlist")
            return self.videos
            
        except Exception as e:
            print(f"Error fetching YouTube playlist: {e}")
            return []


class SpotifyPlaylist:
    """Handles Spotify playlist extraction"""
    
    def __init__(self, playlist_url: str, client_id: str, client_secret: str):
        self.playlist_url = playlist_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.tracks: list[Track] = []
        # Cache mapping Spotify user id -> display name (to avoid repeated API calls)
        self._user_display_cache: dict[str, str] = {}
        
    def extract_playlist_id(self) -> Optional[str]:
        """Extract playlist ID from URL"""
        patterns = [
            r'playlist/([a-zA-Z0-9]+)',
            r'playlist:([a-zA-Z0-9]+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, self.playlist_url)
            if match:
                return match.group(1)
        return None
    
    def fetch_tracks(self) -> list[Track]:
        """Fetch tracks from the playlist using Spotipy"""
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            
            playlist_id = self.extract_playlist_id()
            if not playlist_id:
                print("Error: Could not extract playlist ID from Spotify URL")
                return []
            
            # Initialize Spotify client
            auth_manager = SpotifyClientCredentials(
                client_id=self.client_id,
                client_secret=self.client_secret
            )
            sp = spotipy.Spotify(auth_manager=auth_manager)
            
            # Fetch playlist tracks (handles pagination)
            results = sp.playlist_tracks(playlist_id)
            
            while results:
                for item in results['items']:
                    track = item.get('track')
                    if track:
                        artists = ', '.join([a['name'] for a in track.get('artists', [])])
                        # Spotify API gives 'added_by' and 'added_at' on the playlist item
                        added_by = item.get('added_by') or {}
                        added_by_id = added_by.get('id') if isinstance(added_by, dict) else None
                        added_at = item.get('added_at')
                        added_by_name = None
                        # Try to resolve display name via Spotify API (cached)
                        if added_by_id:
                            try:
                                if added_by_id in self._user_display_cache:
                                    added_by_name = self._user_display_cache[added_by_id]
                                else:
                                    user = sp.user(added_by_id)
                                    name = user.get('display_name') if isinstance(user, dict) else None
                                    if name:
                                        added_by_name = name
                                        self._user_display_cache[added_by_id] = name
                            except Exception:
                                added_by_name = added_by_id
                        track_id = track.get('id', '')
                        
                        self.tracks.append(Track(
                            title=track.get('name', 'Unknown Title'),
                            artist=artists or 'Unknown Artist',
                            platform='spotify',
                            url=track.get('external_urls', {}).get('spotify', ''),
                            uri=f'spotify:track:{track_id}',
                            duration=(track.get('duration_ms') / 1000.0) if track.get('duration_ms') else None,
                            added_by_id=added_by_id,
                            added_by_name=added_by_name,
                            added_at=added_at
                        ))
                
                # Check for more pages
                if results['next']:
                    results = sp.next(results)
                else:
                    results = None
                    
            print(f"✓ Loaded {len(self.tracks)} tracks from Spotify playlist")
            return self.tracks
            
        except Exception as e:
            print(f"Error fetching Spotify playlist: {e}")
            return []


class RandomPlayer:
    """Main player that randomly selects and plays content"""
    
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
        self._youtube_url: str = ''
        self._spotify_url: str = ''
        self._spotify_client_id: str = ''
        self._spotify_client_secret: str = ''
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
        # VR calibration points (can be updated via GUI)
        # Structure:
        #  'base': [(x1,y1),(x2,y2)]
        #  'spotify_last': (x,y)
        #  'youtube_last': (x,y)
        #  'youtube_extra': (x,y)  # extra click for YouTube
        self._vr_points = {
            'base': [(1694, 69), (1640, 127)],
            'spotify_last': (1640, 590),
            'youtube_last': (1640, 640),
            'youtube_extra': (1643, 20),
        }
        # Load persisted play counts from disk if present
        try:
            self._load_play_counts()
        except Exception:
            pass
        # Load persisted VR calibration if present
        try:
            self._load_vr_points()
        except Exception:
            pass

    def _play_counts_path(self) -> str:
        try:
            return os.path.join(os.path.dirname(__file__), 'play_counts.json')
        except Exception:
            return os.path.join('.', 'play_counts.json')

    def _load_play_counts(self):
        """Load play counts from JSON file into self.play_counts."""
        try:
            path = self._play_counts_path()
            if not os.path.exists(path):
                return
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                # ensure integer values
                for k, v in data.items():
                    try:
                        self.play_counts[str(k)] = int(v)
                    except Exception:
                        try:
                            self.play_counts[str(k)] = 0
                        except Exception:
                            pass
        except Exception:
            pass

    def _save_play_counts(self):
        """Atomically save play_counts to JSON on disk."""
        try:
            path = self._play_counts_path()
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.play_counts, f, indent=2, ensure_ascii=False)
            try:
                os.replace(tmp, path)
            except Exception:
                # fallback to rename
                try:
                    os.remove(path)
                except Exception:
                    pass
                try:
                    os.replace(tmp, path)
                except Exception:
                    pass
        except Exception:
            pass

    def _vr_points_path(self) -> str:
        try:
            return os.path.join(os.path.dirname(__file__), 'vr_calibration.json')
        except Exception:
            return os.path.join('.', 'vr_calibration.json')

    def _load_vr_points(self):
        """Load VR calibration points from JSON file into self._vr_points."""
        path = self._vr_points_path()
        if not os.path.exists(path):
            return
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return
        base = data.get('base')
        if isinstance(base, list) and len(base) >= 2:
            try:
                b0 = (int(base[0][0]), int(base[0][1]))
                b1 = (int(base[1][0]), int(base[1][1]))
                self._vr_points['base'] = [b0, b1]
            except Exception:
                pass
        for key in ('spotify_last', 'youtube_last', 'youtube_extra'):
            v = data.get(key)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    self._vr_points[key] = (int(v[0]), int(v[1]))
                except Exception:
                    pass

    def _save_vr_points(self):
        """Atomically save vr calibration to JSON on disk."""
        try:
            path = self._vr_points_path()
            tmp = path + '.tmp'
            out = {}
            try:
                b = self._vr_points.get('base', [])
                if isinstance(b, (list, tuple)) and len(b) >= 2:
                    out['base'] = [[int(b[0][0]), int(b[0][1])], [int(b[1][0]), int(b[1][1])]]
                else:
                    out['base'] = []
            except Exception:
                out['base'] = []
            for key in ('spotify_last', 'youtube_last', 'youtube_extra'):
                try:
                    v = self._vr_points.get(key)
                    if v:
                        out[key] = [int(v[0]), int(v[1])]
                except Exception:
                    pass
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
            try:
                os.replace(tmp, path)
            except Exception:
                try:
                    os.remove(path)
                except Exception:
                    pass
                try:
                    os.replace(tmp, path)
                except Exception:
                    pass
        except Exception:
            pass

    def _focus_tab_by_title(self, search_title: str) -> Optional[gw.Window]:
        """Find and focus a browser tab containing the track title"""
        
        pyautogui.FAILSAFE = False
        
        # Get first few words of title to match (more reliable)
        # Split title and take first 3 words or first 20 chars
        search_words = search_title.lower().split()[:3]
        search_partial = ' '.join(search_words) if search_words else search_title[:20].lower()
        
        all_windows = gw.getAllWindows()
        
        # If not found directly, search through browser tabs
        browser_windows = []
        for win in all_windows:
            if not win.title or not win.title.strip():
                continue
            title_lower = win.title.lower()
            if any(b in title_lower for b in ['chrome', 'edge', 'firefox', 'brave', 'opera']):
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
                
                pyautogui.hotkey('ctrl', 'tab')
                time.sleep(0.2)
                
                new_win = gw.getActiveWindow()
                if new_win and new_win.title == original_title and i > 0:
                    break
        
        return None
        
    def _close_browser_tab(self, search_title: str) -> bool:
        """Find and close a browser tab containing the track title"""
        try:
            pyautogui.FAILSAFE = False

            # Try to focus a tab matching title via helper
            try:
                focused = self._focus_tab_by_title(search_title)
            except Exception:
                focused = None

            if focused:
                try:
                    pyautogui.hotkey('ctrl', 'w')
                    time.sleep(0.2)
                    return True
                except Exception:
                    return False

            return False
        except Exception:
            return False

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
                pyautogui.press('f11')
                time.sleep(0.2)
                pyautogui.press('f')
                time.sleep(0.2)
                pyautogui.press('f')
                time.sleep(0.2)

        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'l')
        pyautogui.hotkey('alt', 'd')
        time.sleep(0.1)
        pyautogui.typewrite(new_url, interval=0.01)
        pyautogui.press('enter')
        time.sleep(0.5)
        pyautogui.press('f11')

        return True

    
    def _focus_spotify_app(self):
        """Find and focus the Spotify desktop app by process name"""
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
        SW_RESTORE = 9
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
                        if process_name == 'spotify.exe':
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
            hwnd = getattr(win, '_hWnd', None)
            if hwnd:
                try:
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
                    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        raise Exception("GetWindowRect failed")

                    hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(mi)
                    if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                        raise Exception("GetMonitorInfoW failed")

                    wr, mr = rect, mi.rcMonitor
                    # Compare window rect to monitor rect with tolerance
                    return (
                        abs(wr.left - mr.left) <= tol and
                        abs(wr.top - mr.top) <= tol and
                        abs(wr.right - mr.right) <= tol and
                        abs(wr.bottom - mr.bottom) <= tol
                    )
                except Exception:
                    # fall through to fallback below
                    pass

            # Fallback: compare window size to primary screen size
            try:
                screen_w, screen_h = pyautogui.size()
                w_w = getattr(win, 'width', None) or getattr(win, 'width', 0)
                w_h = getattr(win, 'height', None) or getattr(win, 'height', 0)
                if w_w is None or w_h is None:
                    return False
                return (w_w >= screen_w - tol and w_h >= screen_h - tol)
            except Exception:
                return False
        except Exception:
            return False


    def perform_vr_reset(self):
        """Bring a browser window to the foreground and click a predefined sequence of points that resets Voice Removal."""
        pyautogui.FAILSAFE = False

        # Try to find a browser window by common browser names
        browser_keywords = ['chrome', 'firefox', 'edge', 'brave', 'opera']
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
            try:
                target_win.activate()
            except Exception:
                pass
            time.sleep(0.3)

        # Detect platform playing state and toggle fullscreen for YouTube
        try:
            is_youtube = (self.current_platform == 'youtube')
        except Exception:
            is_youtube = False
        try:
            is_spotify = (self.current_platform == 'spotify')
        except Exception:
            is_spotify = False

        # Deactivate fullscreen if needed
        if self.is_window_fullscreen(target_win):
            pyautogui.press('f11')
            time.sleep(0.2)
            pyautogui.press('f')
            time.sleep(0.2)
            pyautogui.press('f')
            time.sleep(0.2)


        # Build the points list using calibration if available.
        base = self._vr_points.get('base', [(1694, 69), (1640, 127)])
        if is_youtube:
            last = self._vr_points.get('youtube_last', (1640, 640))
        else:
            last = self._vr_points.get('spotify_last', (1640, 590))

        points = list(base) + [last]
        print(f"   [DEBUG] Performing VR reset (YouTube: {is_youtube}, Spotify: {is_spotify}), points={points}")
        for x, y in points:
            try:
                if pyautogui.pixel(x,y) == (76,255,0):
                    time.sleep(0.1)
                    pyautogui.click(x, y)
                    print("   [DEBUG] Detected green, turning it off and on")
                time.sleep(0.3)
                pyautogui.click(x, y)
                time.sleep(0.1)
            except Exception:
                # If absolute click fails, attempt a small move+click
                try:
                    pyautogui.moveTo(x, y)
                    pyautogui.click()
                except Exception:
                    pass
            time.sleep(0.25)

        if is_youtube:
            try:
                time.sleep(0.2)
                y_extra = self._vr_points.get('youtube_extra', (1643, 20))
                pyautogui.click(y_extra[0], y_extra[1])
                # Press F11 once more before restoring via 'f'
                pyautogui.press('f11')
                time.sleep(0.2)
                # pyautogui.press('f')
            except Exception:
                pass

        # If Spotify was playing, press F11 again and bring the desktop Spotify app back to front
        if is_spotify:
            try:
                time.sleep(0.2)
                pyautogui.press('f11')
            except Exception:
                pass
            try:
                time.sleep(0.1)
                self._focus_spotify_app()
            except Exception:
                pass

    def perform_vr_on(self):
        """Perform VR ON sequence: click first point always; for remaining
        points click only if the pixel at that coordinate is NOT green.
        Returns set of playing pids like perform_vr_reset.
        """

        pyautogui.FAILSAFE = False

        # Find a browser window to activate
        browser_keywords = ['chrome', 'firefox', 'edge', 'brave', 'opera']
        all_windows = gw.getAllWindows()
        target_win = None
        for w in all_windows:
            if w.title and any(b in w.title.lower() for b in browser_keywords):
                target_win = w
                break

        if not target_win and all_windows:
            target_win = all_windows[0]

        if target_win:
            try:
                target_win.activate()
            except Exception:
                pass
            time.sleep(0.3)

        try:
            is_youtube = (self.current_platform == 'youtube')
        except Exception:
            is_youtube = False
        try:
            is_spotify = (self.current_platform == 'spotify')
        except Exception:
            is_spotify = False


        # Determine last point based on platform and calibration
        base = self._vr_points.get('base', [(1694, 69), (1640, 127)])
        if is_youtube:
            last = self._vr_points.get('youtube_last', (1640, 640))
        else:
            last = self._vr_points.get('spotify_last', (1640, 590))

        # Deactivate fullscreen if needed
        if self.is_window_fullscreen(target_win):
            pyautogui.press('f11')
            time.sleep(0.2)
            pyautogui.press('f')
            time.sleep(0.2)
            pyautogui.press('f')
            time.sleep(0.2)

        # Points to click (first is always clicked)
        points = list(base) + [last]

        for idx, (x, y) in enumerate(points):
            try:
                if idx == 0:
                    # Always click first point
                    pyautogui.click(x, y)
                else:
                    try:
                        pix = pyautogui.pixel(x, y)
                    except Exception:
                        pix = None
                    # If pixel is green (76,255,0) skip click
                    if pix is not None and tuple(pix) == (76, 255, 0):
                        # skip
                        pass
                    else:
                        pyautogui.click(x, y)
                time.sleep(0.25)
            except Exception:
                try:
                    pyautogui.moveTo(x, y)
                    pyautogui.click()
                except Exception:
                    pass
            time.sleep(0.25)

        if is_youtube:
            try:
                time.sleep(0.2)
                y_extra = self._vr_points.get('youtube_extra', (1643, 20))
                pyautogui.click(y_extra[0], y_extra[1])
                # Press F11 then restore via 'f'
                pyautogui.press('f11')
                time.sleep(0.2)
                pyautogui.press('f')
            except Exception:
                pass

        if is_spotify:
            try:
                time.sleep(0.2)
                pyautogui.press('f11')
            except Exception:
                pass
            try:
                time.sleep(0.1)
                self._focus_spotify_app()
            except Exception:
                pass


    def perform_vr_off(self):
        """Perform VR OFF sequence: click the first point always; check the
        last point and click it only if it is GREEN (indicates ON), then
        perform the same pre/post F/F11 handling as the other VR routines.
        Returns set of playing pids like perform_vr_on.
        """
        pyautogui.FAILSAFE = False

        # Find a browser window to activate
        browser_keywords = ['chrome', 'firefox', 'edge', 'brave', 'opera']
        all_windows = gw.getAllWindows()
        target_win = None
        for w in all_windows:
            if w.title and any(b in w.title.lower() for b in browser_keywords):
                target_win = w
                break

        if not target_win and all_windows:
            target_win = all_windows[0]

        if target_win:
            try:
                target_win.activate()
            except Exception:
                pass
            time.sleep(0.3)

        try:
            is_youtube = (self.current_platform == 'youtube')
        except Exception:
            is_youtube = False
        try:
            is_spotify = (self.current_platform == 'spotify')
        except Exception:
            is_spotify = False

        # Determine last point based on platform and calibration
        base = self._vr_points.get('base', [(1694, 69), (1640, 127)])
        if is_youtube:
            last = self._vr_points.get('youtube_last', (1640, 640))
        else:
            last = self._vr_points.get('spotify_last', (1640, 590))

        # Deactivate fullscreen if needed
        if self.is_window_fullscreen(target_win):
            pyautogui.press('f11')
            time.sleep(0.2)
            pyautogui.press('f')
            time.sleep(0.2)
            pyautogui.press('f')
            time.sleep(0.2)

        first = tuple(base[0]) if base and len(base) > 0 else (1694, 69)
        # last is already set above

        # Always click first point
        try:
            pyautogui.click(first[0], first[1])
        except Exception:
            try:
                pyautogui.moveTo(first[0], first[1])
                pyautogui.click()
            except Exception:
                pass
        time.sleep(0.3)

        # Check last pixel; click it only if it's green (76,255,0)
        try:
            pix = None
            try:
                pix = pyautogui.pixel(last[0], last[1])
            except Exception:
                pix = None
            if pix is not None and tuple(pix) == (76, 255, 0):
                try:
                    pyautogui.click(last[0], last[1])
                except Exception:
                    try:
                        pyautogui.moveTo(last[0], last[1])
                        pyautogui.click()
                    except Exception:
                        pass
                time.sleep(0.3)
        except Exception:
            pass

        # Post-click state: mirror other routines
        if is_youtube:
            try:
                time.sleep(0.2)
                y_extra = self._vr_points.get('youtube_extra', (1643, 20))
                pyautogui.click(y_extra[0], y_extra[1])
                pyautogui.press('f11')
                time.sleep(0.2)
                pyautogui.press('f')
            except Exception:
                pass

        if is_spotify:
            try:
                time.sleep(0.2)
                pyautogui.press('f11')
            except Exception:
                pass
            try:
                time.sleep(0.1)
                self._focus_spotify_app()
            except Exception:
                pass

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
            tracks = self.all_tracks if platform is None else (self.youtube_tracks if platform == 'youtube' else self.spotify_tracks)
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
        """Stop the currently playing track - closes the tab by searching for the track title"""
        # cancel any autoplay timer
        try:
            if self._autoplay_timer:
                self._autoplay_timer.cancel()
                self._autoplay_timer = None
            # clear bookkeeping
            self._autoplay_start_time = None
            self._autoplay_duration = None
            self._autoplay_remaining = None
            self._autoplay_paused = False
        except Exception:
            pass
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
        try:
            pyautogui.FAILSAFE = False

            focused = False

            # Try to focus the browser tab for the current track title
            if self._current_track_title:
                try:
                    self._focus_tab_by_title(self._current_track_title)
                except Exception:
                    pass

            # Manage autoplay timer bookkeeping: if a timer is active, pause it
            try:
                if self._autoplay_timer and not self._autoplay_paused:
                    try:
                        # compute elapsed and remaining
                        if self._autoplay_start_time and self._autoplay_duration:
                            elapsed = time.time() - (self._autoplay_start_time or time.time())
                            remaining = (self._autoplay_duration or 0) - elapsed
                        else:
                            remaining = None
                        try:
                            self._autoplay_timer.cancel()
                        except Exception:
                            pass
                        self._autoplay_timer = None
                        # store remaining (ensure small minimum)
                        if remaining is None:
                            self._autoplay_remaining = None
                        else:
                            self._autoplay_remaining = max(0.1, float(remaining))
                        self._autoplay_paused = True
                    except Exception:
                        pass
                elif self._autoplay_paused and (self._autoplay_remaining is not None):
                    try:
                        # resume with remaining time
                        self._start_autoplay_timer(self._autoplay_remaining)
                        self._autoplay_remaining = None
                        self._autoplay_paused = False
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                pyautogui.press('space')
                #If playing spotify, focus the app
                if self._spotify_playing or (self.current_platform == 'spotify'):
                    try:
                        self._focus_spotify_app()
                    except Exception:
                        pass
                return True
            except Exception:
                return False
        except Exception:
            return False

    def refresh_current_tab(self) -> bool:
        """Find the currently playing browser tab (by title) and refresh it.

        Returns True if a refresh keypress was attempted, False otherwise.
        """
        pyautogui.FAILSAFE = False
        title = self._current_track_title
        if not title:
            return False

        # Try to focus the tab for the current track
        try:
            focused = self._focus_tab_by_title(title)
        except Exception:
            focused = None

        if not focused:
            return False

        # Attempt Ctrl+R then fall back to F5. If successful, reset autoplay timer
        try:
            refreshed = False
            try:
                pyautogui.hotkey('ctrl', 'r')
                refreshed = True
            except Exception:
                try:
                    pyautogui.press('f5')
                    refreshed = True
                except Exception:
                    refreshed = False

            if not refreshed:
                return False

            # Reset autoplay timer based on the currently playing track (last played)
            try:
                last = None
                if self.played_tracks:
                    last = self.played_tracks[-1]
                if last and getattr(last, 'duration', None):
                    dur = float(last.duration)
                    if last.platform == 'spotify':
                        # trigger a few seconds early for Spotify like in play_track
                        timer_dur = dur - 4 if dur > 4 else dur
                        if timer_dur < 1:
                            timer_dur = max(0.5, dur * 0.9)
                    else:
                        timer_dur = dur
                    try:
                        # start a fresh autoplay timer
                        self._start_autoplay_timer(timer_dur)
                    except Exception:
                        pass
            except Exception:
                pass

            # give a short moment for the refresh to apply
            time.sleep(0.15)
            
            # Press F if youTube to restore fullscreen
            if self.current_platform == 'youtube':
                time.sleep(2)
                pyautogui.press('f')

            return True

        except Exception:
            return False

    def _on_track_end(self):
        """Callback when a track finishes playing."""
        print("\n⏭️  Track finished — auto-playing next track")
        try:
            # clear autoplay bookkeeping (timer already fired)
            try:
                if self._autoplay_timer:
                    try:
                        self._autoplay_timer.cancel()
                    except Exception:
                        pass
                self._autoplay_timer = None
            except Exception:
                pass
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
                try:
                    self._autoplay_timer.cancel()
                except Exception:
                    pass
            # reset bookkeeping for a new timer
            try:
                self._autoplay_start_time = time.time()
            except Exception:
                self._autoplay_start_time = None
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
            self._next_up_window = Menu(self)
            self._next_up_window.start()


    def stop_menu_window(self):
        """Stop the Next Up (Menu) GUI window if running."""
        if self._next_up_window:
            try:
                self._next_up_window.stop()
            except Exception:
                pass
            self._next_up_window = None

    def toggle_show_adder_menu(self):
        """Toggle displaying who added tracks in the Next Up (Menu) window."""
        try:
            self._show_adder_nextup = not getattr(self, '_show_adder_nextup', False)
            status = 'ON' if self._show_adder_nextup else 'OFF'
            print(f"Next Up: show adder {status}")
            # Force immediate refresh
            self.update_menu_file()
        except Exception:
            pass
    def _play_next_from_queue(self):
        """Play the next track from the queue, refilling if necessary."""
        if not self._queue:
            self._fill_queue()
        if self._queue:
            track = self._queue.pop(0)
            self.play_track(track)
        else:
            print("No tracks in queue!")    
    def load_playlists(self, youtube_url: str, spotify_url: str,
                       spotify_client_id: str, spotify_client_secret: str,
                       silent: bool = False):
        """Load both playlists"""
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
            new_spotify_tracks = sp_playlist.fetch_tracks() if not silent else self._fetch_spotify_silent(spotify_url, spotify_client_id, spotify_client_secret)
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
        """Fetch YouTube playlist silently"""
        try:
            import yt_dlp
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
            }
            videos: list[Track] = []
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(url, download=False)
                if result and 'entries' in result:
                    for entry in result['entries']:
                        if entry:
                            vid = entry.get('id', '')
                            duration = entry.get('duration')
                            if duration is None and vid:
                                try:
                                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                                    duration = info.get('duration')
                                except Exception:
                                    duration = None
                            videos.append(Track(
                                title=entry.get('title', 'Unknown'),
                                artist=entry.get('uploader', 'Unknown'),
                                platform='youtube',
                                url=f"https://www.youtube.com/watch?v={vid}",
                                duration=float(duration) if duration else None
                            ))
            return videos
        except Exception:
            return self.youtube_tracks  # Keep existing on error
    
    def _fetch_spotify_silent(self, url: str, client_id: str, client_secret: str) -> list[Track]:
        """Fetch Spotify playlist silently"""
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials
            import re
            
            match = re.search(r'playlist/([a-zA-Z0-9]+)', url)
            if not match:
                return self.spotify_tracks
            playlist_id = match.group(1)
            
            auth_manager = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
            sp = spotipy.Spotify(auth_manager=auth_manager)
            
            tracks: list[Track] = []
            results = sp.playlist_tracks(playlist_id)
            while results:
                for item in results['items']:
                    track = item.get('track')
                    if track:
                        artists = ', '.join([a['name'] for a in track.get('artists', [])])
                        tracks.append(Track(
                            title=track.get('name', 'Unknown'),
                            artist=artists or 'Unknown',
                            platform='spotify',
                            url=track.get('external_urls', {}).get('spotify', ''),
                            uri=f"spotify:track:{track.get('id', '')}",
                            duration=(track.get('duration_ms') / 1000.0) if track.get('duration_ms') else None
                        ))
                results = sp.next(results) if results['next'] else None
            return tracks
        except Exception:
            return self.spotify_tracks  # Keep existing on error
    
    def _refresh_loop(self):
        """Background thread that refreshes playlists periodically"""
        print("🔄 Refresh thread started")
        while not self._stop_refresh.wait(self.refresh_interval):
            self.load_playlists(
                self._youtube_url,
                self._spotify_url,
                self._spotify_client_id,
                self._spotify_client_secret,
                silent=True
            )
    
    def start_auto_refresh(self):
        """Start the background playlist refresh thread"""
        self._stop_refresh.clear()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()
        print(f"\n🔄 Auto-refresh enabled (every {self.refresh_interval}s)")
    
    def stop_auto_refresh(self):
        """Stop the background refresh thread"""
        self._stop_refresh.set()
        if self._refresh_thread:
            self._refresh_thread.join(timeout=1)
        
    def play_track(self, track: Track):
        """Play a track based on its platform"""
        # For YouTube, try to reuse the existing browser tab instead of closing it.
        # For Spotify, we handle closing inline to prevent title change issues.
        if track.platform == 'youtube':
            reused = False
            if self._current_track_title:
                try:
                    reused = self._navigate_in_same_tab(self._current_track_title, track.url)
                except Exception:
                    reused = False
        
        print(f"\n▶️  Now Playing:")
        print(f"   Platform: {track.platform.upper()}")
        print(f"   Title: {track.title}")
        print(f"   Artist: {track.artist}")
        
        if track.platform == 'youtube':
            # Store track title for later tab identification
            self._current_track_title = track.title
            # If we successfully reused an existing tab, flags are set by the helper
            if not locals().get('reused', False):
                # Open YouTube video in browser
                try:
                    webbrowser.open(track.url)
                    self.current_platform = 'youtube'
                    self._youtube_playing = True
                    print(f"   → Opened in browser: {track.url}")
                    # If this is the first played track overall, press F11 after opening
                    try:
                        pyautogui.FAILSAFE = False
                        if len(self.played_tracks) == 0:
                            time.sleep(0.5)
                            pyautogui.press('f11')
                    except Exception:
                        pass

                    # Wait for video to load, then press 'f' for YouTube fullscreen toggle
                    time.sleep(5)
                    try:
                        pyautogui.press('f')
                        print(f"   → Fullscreen activated")
                    except Exception:
                        pass
                    # start autoplay timer if duration known
                    try:
                        if track.duration:
                            self._start_autoplay_timer(track.duration)
                    except Exception:
                        pass
                except Exception:
                    webbrowser.open(track.url)
                    print(f"   → Opened in browser: {track.url}")
            else:
                # Reused existing tab — set playing flags
                self.current_platform = 'youtube'
                self._youtube_playing = True
                print(f"   → Reused existing browser tab for: {track.url}")
                # Wait for video to load, then press 'f' for YouTube fullscreen toggle
                try:
                    time.sleep(5)
                    pyautogui.press('f')
                    print(f"   → Fullscreen activated (reused tab)")
                except Exception:
                    pass
                # start autoplay timer if duration known
                try:
                    if track.duration:
                        self._start_autoplay_timer(track.duration)
                except Exception:
                    pass
            
        elif track.platform == 'spotify':
            # Try to reuse the existing browser tab (YouTube or Spotify) before closing
            reused = False
            if self._current_track_title:
                try:
                    reused = self._navigate_in_same_tab(self._current_track_title, track.url)
                except Exception:
                    reused = False

            if reused:
                # Reused tab for Spotify URL
                self._current_track_title = track.title
                self.current_platform = 'spotify'
                self._spotify_playing = True
                print(f"   → Reused existing browser tab for: {track.url}")
                # Give page some time to load and then focus desktop Spotify app if desired
                time.sleep(3.5)
                try:
                    self._focus_spotify_app()
                except Exception:
                    pass
                # start autoplay timer if duration known — trigger 3s early for Spotify
                try:
                    if track.duration:
                        timer_dur = track.duration - 5 if track.duration > 5 else track.duration
                        if timer_dur < 1:
                            timer_dur = max(0.5, track.duration * 0.9)
                        self._start_autoplay_timer(timer_dur)
                except Exception:
                    pass
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
                    print(f"   [DEBUG] No Spotify tab to close. Playing={self._spotify_playing}, Title={self._current_track_title}")

                # Now update title and open new tab
                self._current_track_title = track.title

                # Open Spotify in browser, then focus the desktop app
                if track.url:
                    try:
                        webbrowser.open(track.url)
                        self.current_platform = 'spotify'
                        self._spotify_playing = True
                        print(f"   → Opened in browser: {track.url}")
                        # If this is the first played track overall, press F11 after opening
                        try:
                            pyautogui.FAILSAFE = False
                            if len(self.played_tracks) == 0:
                                time.sleep(0.5)
                                pyautogui.press('f11')
                                print(f"   → F11 pressed (first track)")
                        except Exception:
                            pass

                        # Focus the Spotify desktop app by finding its process
                        time.sleep(3)
                        self._focus_spotify_app()
                        # start autoplay timer if duration known — trigger 3s early for Spotify
                        try:
                            if track.duration:
                                timer_dur = track.duration - 4 if track.duration > 4 else track.duration
                                # ensure at least 1 second
                                if timer_dur < 1:
                                    timer_dur = max(0.5, track.duration * 0.9)
                                self._start_autoplay_timer(timer_dur)
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"   [DEBUG] Exception: {e}")
                        webbrowser.open(track.url)
                        print(f"   → Opened in browser: {track.url}")
        
        self.played_tracks.append(track)

        # increment play count
        try:
            key = self._track_key(track)
            self.play_counts[key] = self.play_counts.get(key, 0) + 1
            print(f"   [DEBUG] Play count for '{key}': {self.play_counts[key]}")
            try:
                # persist updated counts
                self._save_play_counts()
            except Exception:
                pass
        except Exception:
            pass
        
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



            
class Menu:
    """A small Tkinter window that displays the upcoming queue and refreshes automatically."""
    def __init__(self, player: 'RandomPlayer'):
        self.player = player
        self._thread: Optional[threading.Thread] = None
        self.root = None
        self._running = threading.Event()
        self._scroll_index = 0
        self._queue_snapshot_hash = None
        self._top_lbl = None
        self._listbox = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self._running.clear()
            if self.root:
                try:
                    self.root.quit()
                except Exception:
                    pass
        except Exception:
            pass

    def schedule_update(self, queue_snapshot: list):
        """Schedule an immediate UI update from any thread.

        Displays the first track in a fixed label and fills the scrolling
        listbox with the remaining queue entries. Resets scroll index when
        the queue changes.
        """
        try:
            if not self.root:
                return

            def _update():
                try:
                    top_lbl = getattr(self, '_top_lbl', None)
                    tree = getattr(self, '_tree', None)
                    listbox = getattr(self, '_listbox', None)
                    # require top area and at least one of tree/listbox
                    if top_lbl is None or (tree is None and listbox is None):
                        return
                    # Update top "Next up"
                    if not queue_snapshot:
                        top_lbl.config(text='Next up: (none)')
                        listbox.delete(0, 'end')
                        return

                    first = queue_snapshot[0]
                    try:
                        title_short = (first.title[:30]) if first.title else ''
                    except Exception:
                        title_short = ''
                    try:
                        artist_short = (first.artist[:20]) if first.artist else ''
                    except Exception:
                        artist_short = ''
                    # Support both Label and Text widgets for the top area.
                    try:
                        # Build top text; if adder display is enabled, add second line with adder
                        top_text = f"Next: {title_short} — {artist_short}"
                        if getattr(self.player, '_show_adder_nextup', False):
                            ab = getattr(first, 'added_by_name', None) or getattr(first, 'added_by_id', None)
                            if ab:
                                try:
                                    ab_short = ab[:30] if isinstance(ab, str) else str(ab)
                                except Exception:
                                    ab_short = str(ab)
                                top_text = top_text + "\nAdded by: " + ab_short

                        # If it's a Text widget, replace contents and keep it readonly
                        if hasattr(top_lbl, 'delete') and hasattr(top_lbl, 'insert'):
                            try:
                                top_lbl.config(state='normal')
                            except Exception:
                                pass
                            try:
                                top_lbl.delete('1.0', 'end')
                            except Exception:
                                pass
                            try:
                                top_lbl.insert('1.0', top_text)
                            except Exception:
                                pass
                            try:
                                top_lbl.config(state='disabled')
                            except Exception:
                                pass
                        else:
                            top_lbl.config(text=top_text)
                    except Exception:
                        try:
                            top_lbl.config(text=f"Next: {title_short} — {artist_short}")
                        except Exception:
                            pass

                    # Prepare list of tracks 
                    rest = []
                    # Enumerate tracks and show 1-based ordinals (1,2,3,...).
                    # The top area still highlights the first item, but the
                    # table/list will include it as well.
                    for idx, t in enumerate(queue_snapshot, start=1):
                        try:
                            t_title = t.title[:30] if t.title else ''
                        except Exception:
                            t_title = ''
                        try:
                            t_artist = t.artist[:20] if t.artist else ''
                        except Exception:
                            t_artist = ''
                        # Fixed-width index (right-aligned 3 chars) with dot and a space
                        prefix = f"{idx:>3}. "
                        # Build fixed-width table columns for index, title, artist, optional adder
                        if getattr(self.player, '_show_adder_nextup', False):
                            ab = getattr(t, 'added_by_name', None) or getattr(t, 'added_by_id', None)
                            ab_short = ''
                            if ab:
                                try:
                                    ab_short = (ab[:18]) if isinstance(ab, str) else str(ab)
                                except Exception:
                                    ab_short = str(ab)
                            line = f"{prefix}{t_title:<30} {t_artist:<20} {ab_short:<18}"
                        else:
                            line = f"{prefix}{t_title:<30} {t_artist:<20}"
                        rest.append(line)

                    # Update header to reflect current adder toggle
                    try:
                        hdr = getattr(self, '_header_lbl', None)
                        if hdr is not None:
                            show_adder = getattr(self.player, '_show_adder_nextup', False)
                            header_text = f"{'#':>3}. {'Title':30} {'Artist':20} {'Adder' if show_adder else ''}"
                            try:
                                hdr.config(text=header_text)
                            except Exception:
                                pass

                    except Exception:
                        pass

                    # If queue changed, reset scroll index
                    new_hash = hash(tuple(rest))
                    if new_hash != self._queue_snapshot_hash:
                        self._queue_snapshot_hash = new_hash
                        self._scroll_index = 0
                    # Update tree/listbox contents
                    if tree is not None:
                        try:
                            # show/hide adder column based on toggle
                            if getattr(self.player, '_show_adder_nextup', False):
                                tree['displaycolumns'] = ('idx', 'title', 'artist', 'adder')
                            else:
                                tree['displaycolumns'] = ('idx', 'title', 'artist')
                        except Exception:
                            pass
                        try:
                            for ch in tree.get_children():
                                tree.delete(ch)
                        except Exception:
                            pass
                        # Adjust column widths when adder column is toggled so the
                        # visible columns reflow to sensible sizes.
                        try:
                            show_adder = getattr(self.player, '_show_adder_nextup', False)
                            try:
                                self._reflow_columns(getattr(self, '_tree', None), show_adder)
                            except Exception:
                                pass
                        except Exception:
                            pass
                        # Insert rows into tree; rest already contains formatted lines but we also insert structured values if available
                        for i, t in enumerate(queue_snapshot, start=1):
                            try:
                                title_short = (t.title[:30]) if t.title else ''
                            except Exception:
                                title_short = ''
                            try:
                                artist_short = (t.artist[:20]) if t.artist else ''
                            except Exception:
                                artist_short = ''
                            ab = getattr(t, 'added_by_name', None) or getattr(t, 'added_by_id', None)
                            ab_short = ''
                            if getattr(self.player, '_show_adder_nextup', False) and ab:
                                try:
                                    ab_short = (ab[:18]) if isinstance(ab, str) else str(ab)
                                except Exception:
                                    ab_short = str(ab)
                            try:
                                tree.insert('', 'end', iid=str(i), values=(i, title_short, artist_short, ab_short))
                            except Exception:
                                try:
                                    tree.insert('', 'end', values=(i, title_short, artist_short, ab_short))
                                except Exception:
                                    pass
                    else:
                        listbox.delete(0, 'end')
                        for item in rest:
                            listbox.insert('end', item)
                except Exception:
                    pass

            try:
                self.root.after(0, _update)
            except Exception:
                pass
        except Exception:
            pass

    def _reflow_columns(self, tree, show_adder: bool):
        """Set Treeview column widths as percentages of available width.

        `show_adder` controls whether the `adder` column is shown and sized.
        """
        try:
            if tree is None:
                return
            # Prefer the tree's width; fallback to root width
            try:
                total_w = tree.winfo_width()
                total_w -= 20 # adjust for padding
            except Exception:
                total_w = 0
            if not total_w or total_w < 50:
                try:
                    total_w = self.root.winfo_width()
                except Exception:
                    total_w = 1080

            # Reserve a bit for vertical scrollbar and padding
            vsb_reserve = 20
            padding = 24
            avail = max(200, total_w - vsb_reserve - padding)

            if show_adder:
                idx_pct = 0.04
                title_pct = 0.54
                artist_pct = 0.24
                adder_pct = 0.18
            else:
                idx_pct = 0.04
                title_pct = 0.74
                artist_pct = 0.22
                adder_pct = 0.0

            idx_w = max(30, int(avail * idx_pct))
            title_w = max(120, int(avail * title_pct))
            artist_w = max(80, int(avail * artist_pct))

            try:
                tree.column('idx', width=idx_w, anchor='e')
                tree.column('title', width=title_w, anchor='w')
                tree.column('artist', width=artist_w, anchor='w')
                if show_adder:
                    adder_w = max(60, int(avail * adder_pct))
                    tree.column('adder', width=adder_w, anchor='w')
                else:
                    try:
                        tree.column('adder', width=0)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

    def _run(self):
        try:
            import tkinter as tk
        except Exception:
            return

        try:
            self.root = tk.Tk()
            self.root.title('Next Up')
            self.root.geometry('1080x960')

            # Top area: always-visible "Next" label
            try:
                top_font = None
                import tkinter.font as tkfont
                top_font = tkfont.Font(size=48, weight='bold')
            except Exception:
                top_font = None

            # Use a two-line Text widget for the top area so text can wrap.
            try:
                bg = self.root.cget('bg')
            except Exception:
                bg = None

            if top_font:
                top_txt = tk.Text(self.root, name='nextup_top', height=2, wrap='word', font=top_font, bd=0, relief='flat')
            else:
                top_txt = tk.Text(self.root, name='nextup_top', height=2, wrap='word', bd=0, relief='flat')
            if bg is not None:
                try:
                    top_txt.config(bg=bg)
                except Exception:
                    pass
            try:
                top_txt.config(state='disabled')
            except Exception:
                pass
            top_txt.pack(fill='x', padx=8, pady=(8,4))
            self._top_lbl = top_txt

            # Control buttons placed between the top 'Next' area and the table
            try:
                try:
                    import tkinter.font as _tkfont
                    btn_font = _tkfont.Font(size=18, weight='bold')
                except Exception:
                    btn_font = None

                btn_frame = tk.Frame(self.root)
                btn_frame.pack(fill='x', padx=8, pady=(4,4))
                # Second row for less-frequently used VR buttons
                btn_frame2 = tk.Frame(self.root)
                btn_frame2.pack(fill='x', padx=8, pady=(0,8))

                def _shuffle_queue():
                    try:
                        with self.player._playlist_lock:
                            random.shuffle(self.player._queue)
                        print("Queue shuffled (NextUp window)")
                        try:
                            self.player.update_menu_file()
                        except Exception:
                            pass
                    except Exception:
                        pass

                def _next_track():
                    try:
                        self.player._play_next_from_queue()
                        try:
                            self.player.update_menu_file()
                        except Exception:
                            pass
                    except Exception:
                        pass

                def _pause_playback():
                    try:
                        try:
                            res = self.player.pause_playback()
                            print(f"Playback pause/unpause attempted: {res}")
                        except Exception:
                            pass
                    except Exception:
                        pass

                def _reset_vr():
                    try:
                        # Record button center so we can return the mouse to it afterwards
                        try:
                            bx = b_reset.winfo_rootx()
                            by = b_reset.winfo_rooty()
                            bw = b_reset.winfo_width()
                            bh = b_reset.winfo_height()
                            btn_center = (bx + bw // 2, by + bh // 2)
                        except Exception:
                            btn_center = None

                        # Trigger the VR routine (same as 'vr' command)
                        try:
                            self.player.perform_vr_reset()
                        except Exception:
                            pass

                        # Move mouse back to the button
                        if btn_center is not None:
                            try:
                                pyautogui.FAILSAFE = False
                                pyautogui.moveTo(btn_center[0], btn_center[1])
                            except Exception:
                                pass

                        print("VR reset triggered from NextUp window")
                    except Exception:
                        pass

                def _refresh_tab():
                    try:
                        try:
                            bx = b_refresh.winfo_rootx()
                            by = b_refresh.winfo_rooty()
                            bw = b_refresh.winfo_width()
                            bh = b_refresh.winfo_height()
                            btn_center = (bx + bw // 2, by + bh // 2)
                        except Exception:
                            btn_center = None

                        try:
                            res = self.player.refresh_current_tab()
                            print(f"Refresh attempted: {res}")
                        except Exception:
                            pass

                        if btn_center is not None:
                            try:
                                pyautogui.FAILSAFE = False
                                pyautogui.moveTo(btn_center[0], btn_center[1])
                            except Exception:
                                pass
                    except Exception:
                        pass

                def _toggle_adder():
                    try:
                        try:
                            self.player.toggle_show_adder_menu()
                        except Exception:
                            pass
                        try:
                            self.player.update_menu_file()
                        except Exception:
                            pass
                    except Exception:
                        pass

                def _toggle_qr_display():
                    try:
                        # If visible, hide the QR frame
                        if getattr(self, '_qr_frame', None) and getattr(self, '_qr_visible', False):
                            try:
                                self._qr_frame.pack_forget()
                            except Exception:
                                pass
                            self._qr_visible = False
                            return

                        # Ensure qrcodes dir exists
                        try:
                            import glob
                            from PIL import Image, ImageTk
                        except Exception:
                            # Pillow not available or import failed
                            return

                        qr_dir = os.path.join(os.path.dirname(__file__), 'qrcodes')
                        try:
                            png_paths = sorted(glob.glob(os.path.join(qr_dir, '*.png')))
                        except Exception:
                            png_paths = []

                        if not png_paths:
                            # nothing to show
                            return

                        # Create or clear frame
                        if not getattr(self, '_qr_frame', None):
                            self._qr_frame = tk.Frame(self.root)
                        else:
                            for ch in self._qr_frame.winfo_children():
                                try:
                                    ch.destroy()
                                except Exception:
                                    pass

                        # Keep references to PhotoImage to avoid GC
                        self._qr_images_refs = []

                        # Load images and display side-by-side with captions
                        for p in png_paths:
                            try:
                                sub = tk.Frame(self._qr_frame)
                                sub.pack(side='left', padx=6, pady=4)
                                img = Image.open(p)
                                # Resize to a consistent height while keeping aspect
                                target_h = 400
                                w = int(img.width * (target_h / img.height)) if img.height else img.width
                                img = img.resize((w, target_h), Image.LANCZOS)
                                photo = ImageTk.PhotoImage(img)
                                lbl = tk.Label(sub, image=photo, bd=0)
                                lbl.pack(side='top')

                                # Add text caption showing which QR this is (filename)
                                name = os.path.splitext(os.path.basename(p))[0]

                                cap = tk.Label(sub, text=name, anchor='center')
                                cap.pack(side='top', pady=(4,0))
                                self._qr_images_refs.append(photo)
                            except Exception:
                                pass

                        # Pack the frame between the buttons and the queue frame
                        self._qr_frame.pack(fill='x', padx=8, pady=(4,4), before=frame)

                        self._qr_visible = True
                    except Exception:
                        pass

                def _quit_app():
                    try:
                        try:
                            self.player.stop_current(wait_after=False)
                        except Exception:
                            pass
                        try:
                            self.player.stop_auto_refresh()
                        except Exception:
                            pass
                        try:
                            self.player.stop_menu_window()
                        except Exception:
                            pass
                        # Attempt a clean shutdown of the process
                        try:
                            import os
                            os._exit(0)
                        except Exception:
                            try:
                                self.root.quit()
                            except Exception:
                                pass
                    except Exception:
                        pass

                def _vr_on():
                    try:
                        try:
                            bx = b_vron.winfo_rootx()
                            by = b_vron.winfo_rooty()
                            bw = b_vron.winfo_width()
                            bh = b_vron.winfo_height()
                            btn_center = (bx + bw // 2, by + bh // 2)
                        except Exception:
                            btn_center = None
                        try:
                            self.player.perform_vr_on()
                        except Exception:
                            pass
                        if btn_center is not None:
                            try:
                                pyautogui.FAILSAFE = False
                                pyautogui.moveTo(btn_center[0], btn_center[1])
                            except Exception:
                                pass
                    except Exception:
                        pass

                def _vroff():
                    try:
                        try:
                            bx = b_vroff.winfo_rootx()
                            by = b_vroff.winfo_rooty()
                            bw = b_vroff.winfo_width()
                            bh = b_vroff.winfo_height()
                            btn_center = (bx + bw // 2, by + bh // 2)
                        except Exception:
                            btn_center = None
                        try:
                            self.player.perform_vr_off()
                        except Exception:
                            pass
                        if btn_center is not None:
                            try:
                                pyautogui.FAILSAFE = False
                                pyautogui.moveTo(btn_center[0], btn_center[1])
                            except Exception:
                                pass
                    except Exception:
                        pass

                def _calibrate_vr():
                    try:
                        # Dialog to guide the user through capturing points by pressing Enter
                        dlg = tk.Toplevel(self.root)
                        dlg.title('Calibrate VR')
                        dlg.geometry('560x220')
                        dlg.transient(self.root)

                        # We always capture all calibration points in sequence:
                        # base1, base2, spotify_last, youtube_last, youtube_extra
                        status = tk.Label(dlg, text='Press Start to begin. Hover over each point and press Enter to capture. Before starting calibration open a youtube video and fullscreen the browser window via f11 and then fullscreen the video via F. You can go back to maximized window after doing this. Do not ask why,f you don\'t wanna know', wraplength=520, justify='left')
                        status.pack(fill='x', padx=8, pady=(8,4))

                        pos_lbl = tk.Label(dlg, text='Current mouse: (x, y)')
                        pos_lbl.pack(anchor='w', padx=8)

                        btn_frame_cal = tk.Frame(dlg)
                        btn_frame_cal.pack(fill='x', pady=8, padx=8)

                        capturing = {'active': False}
                        steps = []
                        captures = {}

                        def update_pos():
                            try:
                                if not dlg.winfo_exists():
                                    return
                                p = pyautogui.position()
                                pos_lbl.config(text=f'Current mouse: ({p[0]}, {p[1]})')
                                if capturing.get('active'):
                                    dlg.after(100, update_pos)
                                else:
                                    # keep updating briefly so user can move
                                    dlg.after(300, update_pos)
                            except Exception:
                                pass

                        def start_capture():
                            try:
                                # Always capture full sequence
                                seq = ['base1', 'base2', 'spotify_last', 'youtube_last', 'youtube_extra']
                                steps.clear()
                                for s in seq:
                                    steps.append(s)

                                captures.clear()
                                capturing['active'] = True

                                status.config(text=f"Step 1/{len(steps)}: Hover on Karaoke Monster Extension. Make sure to click this window last and press Enter to capture")
                                dlg.focus_force()
                                dlg.bind('<Return>', on_enter)
                                update_pos()
                            except Exception:
                                pass

                        def on_enter(event=None):
                            descriptions = {'base1': 'Hover on Karaoke Monster Extension',
                            'base2': 'Click on Karaoke Monster Extension and Hover on the left half of Master Switch (the one at the top). Make sure the position you hovered is green when the switch is on.',
                            'spotify_last': 'Hover on the VR switch when spotify is on the tab. Sometimes when having multiple youtube and or spotify tabs open the extension will shoe a button "Use current", click it before performing this step. ',
                            'youtube_last': 'Hover on the VR switch when youtube is on the tab. Sometimes when having multiple youtube and or spotify tabs open the extension will shoe a button "Use current", click it before performing this step. ',
                            'youtube_extra': 'Hover on the top of the browser window, somewhere where there is no tab (this is to focus the browser window without changing the tab)'
                            }
                            try:
                                if not capturing.get('active'):
                                    return
                                p = pyautogui.position()
                                idx = len(captures)
                                step_name = steps[idx]
                                captures[step_name] = (p[0], p[1])
                                # advance
                                if len(captures) >= len(steps):
                                    # finished
                                    capturing['active'] = False
                                    dlg.unbind('<Return>')
                                    # Apply captured points to player
                                    try:
                                        # base
                                        if 'base1' in captures and 'base2' in captures:
                                            self.player._vr_points['base'] = [captures['base1'], captures['base2']]
                                        if 'spotify_last' in captures:
                                            self.player._vr_points['spotify_last'] = captures['spotify_last']
                                        if 'youtube_last' in captures:
                                            self.player._vr_points['youtube_last'] = captures['youtube_last']
                                        if 'youtube_extra' in captures:
                                            self.player._vr_points['youtube_extra'] = captures['youtube_extra']
                                        try:
                                            self.player._save_vr_points()
                                        except Exception:
                                            pass
                                        print(f"VR calibration saved: {self.player._vr_points}")
                                    except Exception:
                                        pass
                                    status.config(text='Calibration complete. You can close this window.')
                                    return
                                else:
                                    # next step
                                    next_idx = len(captures)
                                    status.config(text=f'Step {next_idx+1}/{len(steps)}: {descriptions.get(steps[next_idx])} Make sure to click this window last and press Enter to capture')
                            except Exception:
                                pass

                        def cancel():
                            try:
                                capturing['active'] = False
                                try:
                                    dlg.unbind('<Return>')
                                except Exception:
                                    pass
                                dlg.destroy()
                            except Exception:
                                pass

                        b_start = tk.Button(btn_frame_cal, text='Start', command=start_capture)
                        b_cancel = tk.Button(btn_frame_cal, text='Cancel', command=cancel)
                        b_start.pack(side='left', padx=8)
                        b_cancel.pack(side='left', padx=8)

                        # kick off pos updates
                        dlg.after(100, update_pos)
                        dlg.focus_force()
                    except Exception:
                        pass

                try:
                    if btn_font:
                        b_shuffle = tk.Button(btn_frame, text='Shuffle', command=_shuffle_queue, font=btn_font, padx=16, pady=8)
                        b_next = tk.Button(btn_frame, text='Next', command=_next_track, font=btn_font, padx=16, pady=8)
                        b_pause = tk.Button(btn_frame, text='Pause', command=_pause_playback, font=btn_font, padx=12, pady=6)
                        b_refresh = tk.Button(btn_frame, text='Reset Tab', command=_refresh_tab, font=btn_font, padx=12, pady=6)
                        b_reset = tk.Button(btn_frame2, text='Reset VR', command=_reset_vr, font=btn_font, padx=16, pady=8)
                        b_vron = tk.Button(btn_frame2, text='VR ON', command=_vr_on, font=btn_font, padx=16, pady=8)
                        b_vroff = tk.Button(btn_frame2, text='VR OFF', command=_vroff, font=btn_font, padx=16, pady=8)
                        b_calibrate = tk.Button(btn_frame2, text='Calibrate VR', command=_calibrate_vr, font=btn_font, padx=12, pady=6)
                        b_adder = tk.Button(btn_frame, text='Adder', command=_toggle_adder, font=btn_font, padx=12, pady=6)
                        b_qr = tk.Button(btn_frame, text='QR', command=_toggle_qr_display, font=btn_font, padx=10, pady=4)
                        b_quit = tk.Button(btn_frame, text='Quit', command=_quit_app, font=btn_font, padx=12, pady=6)
                    else:
                        b_shuffle = tk.Button(btn_frame, text='Shuffle', command=_shuffle_queue, padx=12, pady=6)
                        b_next = tk.Button(btn_frame, text='Next', command=_next_track, padx=12, pady=6)
                        b_pause = tk.Button(btn_frame, text='Pause', command=_pause_playback, padx=10, pady=4)
                        b_refresh = tk.Button(btn_frame, text='Reset Tab', command=_refresh_tab, padx=10, pady=4)
                        b_reset = tk.Button(btn_frame2, text='Reset VR', command=_reset_vr, padx=12, pady=6)
                        b_vron = tk.Button(btn_frame2, text='VR ON', command=_vr_on, padx=12, pady=6)
                        b_vroff = tk.Button(btn_frame2, text='VR OFF', command=_vroff, padx=12, pady=6)
                        b_calibrate = tk.Button(btn_frame2, text='Calibrate VR', command=_calibrate_vr, padx=10, pady=4)
                        b_adder = tk.Button(btn_frame, text='Adder', command=_toggle_adder, padx=10, pady=4)
                        b_qr = tk.Button(btn_frame, text='QR', command=_toggle_qr_display, padx=10, pady=4)
                        b_quit = tk.Button(btn_frame, text='Quit', command=_quit_app, padx=10, pady=4)
                    b_shuffle.pack(side='left', padx=8, pady=4)
                    b_next.pack(side='left', padx=8, pady=4)
                    # Pause placed next to Next for quick access
                    try:
                        b_pause.pack(side='left', padx=8, pady=4)
                        try:
                            b_refresh.pack(side='left', padx=8, pady=4)
                        except Exception:
                            pass
                    except Exception:
                        pass
                    # VR buttons live on the second row
                    b_reset.pack(in_=btn_frame2, side='left', padx=8, pady=4)
                    b_vron.pack(in_=btn_frame2, side='left', padx=8, pady=4)
                    b_vroff.pack(in_=btn_frame2, side='left', padx=8, pady=4)
                    b_calibrate.pack(in_=btn_frame2, side='left', padx=8, pady=4)
                    # Pack adder and quit on the right side for accessibility
                    b_adder.pack(side='right', padx=8, pady=4)
                    b_qr.pack(side='right', padx=8, pady=4)
                    b_quit.pack(side='right', padx=8, pady=4)
                except Exception:
                    pass

            except Exception:
                pass

            # Scrolling table for the rest (Treeview for columns)
            frame = tk.Frame(self.root)
            frame.pack(fill='both', expand=True, padx=8, pady=(0,8))

            try:
                list_font = tkfont.Font(family='Consolas', size=24)
            except Exception:
                list_font = None

            try:
                from tkinter import ttk
            except Exception:
                ttk = None

            # Create a Treeview with columns: idx, title, artist, adder
            if ttk:
                style = ttk.Style()
                try:
                    if list_font:
                        # Try to set Treeview row height to match font linespace
                        try:
                            row_h = list_font.metrics('linespace')
                        except Exception:
                            try:
                                # Fallback to font size if metrics unavailable
                                row_h = int(list_font.cget('size'))
                            except Exception:
                                row_h = None
                        cfg = {'font': list_font}
                        if row_h:
                            cfg['rowheight'] = row_h
                        try:
                            style.configure('Treeview', **cfg)
                        except Exception:
                            # Final fallback: configure font only
                            try:
                                style.configure('Treeview', font=list_font)
                            except Exception:
                                pass
                except Exception:
                    pass

                tree = ttk.Treeview(frame, columns=('idx', 'title', 'artist', 'adder'), show='headings', height=32)
                # Setup headings
                tree.heading('idx', text='#')
                tree.heading('title', text='Title')
                tree.heading('artist', text='Artist')
                tree.heading('adder', text='Adder')
                # Column widths will be set dynamically to percentages
                try:
                    # initial sizing
                    self._reflow_columns(tree, getattr(self.player, '_show_adder_nextup', False))
                except Exception:
                    # fallback to reasonable defaults
                    try:
                        tree.column('idx', width=50, anchor='e')
                        tree.column('title', width=420, anchor='w')
                        tree.column('artist', width=260, anchor='w')
                        tree.column('adder', width=200, anchor='w')
                    except Exception:
                        pass

                vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
                tree.configure(yscrollcommand=vsb.set)
                tree.pack(side='left', fill='both', expand=True)
                vsb.pack(side='right', fill='y')
                self._tree = tree
                # Reflow columns when the frame or root resizes
                try:
                    frame.bind('<Configure>', lambda e: self._reflow_columns(tree, getattr(self.player, '_show_adder_nextup', False)))
                except Exception:
                    pass
                try:
                    self.root.bind('<Configure>', lambda e: self._reflow_columns(tree, getattr(self.player, '_show_adder_nextup', False)))
                except Exception:
                    pass
                # Drag-and-drop support for reordering the queue
                try:
                    def _on_tree_button_press(event):
                        try:
                            item = tree.identify_row(event.y)
                            if not item:
                                return
                            self._dragging = True
                            self._drag_iid = item
                            try:
                                tree.selection_set(item)
                            except Exception:
                                pass
                        except Exception:
                            pass

                    def _on_tree_motion(event):
                        try:
                            if not getattr(self, '_dragging', False):
                                return
                            over = tree.identify_row(event.y)
                            if over:
                                try:
                                    tree.selection_set(over)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    def _on_tree_button_release(event):
                        try:
                            if not getattr(self, '_dragging', False):
                                return
                            from_iid = getattr(self, '_drag_iid', None)
                            self._dragging = False
                            self._drag_iid = None
                            if from_iid is None:
                                return
                            target = tree.identify_row(event.y)
                            with self.player._playlist_lock:
                                # Map tree iid (string int) to player._queue index: iid N -> queue index N-1
                                try:
                                    from_idx = int(from_iid) - 1
                                except Exception:
                                    from_idx = None
                                try:
                                    to_idx = int(target) - 1 if target else None
                                except Exception:
                                    to_idx = None

                                if from_idx is None or from_idx < 0 or from_idx >= len(self.player._queue):
                                    return

                                # Remove the item from the queue
                                item = self.player._queue.pop(from_idx)

                                if to_idx is None:
                                    # Append to end
                                    self.player._queue.append(item)
                                else:
                                    # If removing earlier in list shifts target index
                                    if from_idx < to_idx:
                                        # After pop, target index decreases by 1
                                        to_idx = max(0, to_idx)
                                    # Insert before target position
                                    insert_at = min(max(0, to_idx), len(self.player._queue))
                                    self.player._queue.insert(insert_at, item)

                            # Refresh UI to reflect new ordering
                            try:
                                self.player.update_menu_file()
                            except Exception:
                                pass
                        except Exception:
                            pass

                    tree.bind('<ButtonPress-1>', _on_tree_button_press)
                    tree.bind('<B1-Motion>', _on_tree_motion)
                    tree.bind('<ButtonRelease-1>', _on_tree_button_release)
                    try:
                        def _on_tree_double_click(event):
                            try:
                                item = tree.identify_row(event.y)
                                if not item:
                                    return
                                try:
                                    idx = int(item) - 1
                                except Exception:
                                    return
                                with self.player._playlist_lock:
                                    if idx < 0 or idx >= len(self.player._queue):
                                        return
                                    track = self.player._queue.pop(idx)
                                try:
                                    self.player.play_track(track)
                                except Exception:
                                    pass
                                try:
                                    self.player.update_menu_file()
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        tree.bind('<Double-1>', _on_tree_double_click)
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                # Fallback to Listbox if ttk unavailable
                try:
                    listbox = tk.Listbox(frame, name='nextup_list', activestyle='none', height=10)
                    if list_font:
                        listbox.config(font=list_font)
                    scrollbar = tk.Scrollbar(frame, orient='vertical', command=listbox.yview)
                    listbox.config(yscrollcommand=scrollbar.set)
                    listbox.pack(side='left', fill='both', expand=True)
                    scrollbar.pack(side='right', fill='y')
                    self._tree = None
                    self._listbox = listbox
                    try:
                        def _on_listbox_double(event):
                            try:
                                sel = listbox.curselection()
                                if not sel:
                                    return
                                idx = int(sel[0])
                                with self.player._playlist_lock:
                                    if idx < 0 or idx >= len(self.player._queue):
                                        return
                                    track = self.player._queue.pop(idx)
                                try:
                                    self.player.play_track(track)
                                except Exception:
                                    pass
                                try:
                                    self.player.update_menu_file()
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        listbox.bind('<Double-1>', _on_listbox_double)
                    except Exception:
                        pass
                except Exception:
                    self._tree = None
                    self._listbox = None

            # Scrolling callback
            def scroll_step():
                try:
                    if not self._running.is_set():
                        return
                    tree = getattr(self, '_tree', None)
                    if tree is not None:
                        children = tree.get_children()
                        size = len(children)
                        if size > 0:
                            idx = self._scroll_index % size
                            try:
                                item = children[idx]
                                try:
                                    tree.selection_set(item)
                                except Exception:
                                    pass
                                try:
                                    tree.see(item)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            self._scroll_index = (self._scroll_index + 1) % max(1, size)
                    else:
                        lb = getattr(self, '_listbox', None)
                        if lb is None:
                            return
                        size = lb.size()
                        if size > 0:
                            idx = self._scroll_index % size
                            try:
                                lb.selection_clear(0, 'end')
                                lb.selection_set(idx)
                                lb.see(idx)
                            except Exception:
                                pass
                            self._scroll_index = (self._scroll_index + 1) % max(1, size)
                    self.root.after(1000, scroll_step)
                except Exception:
                    pass

            def refresh_loop():
                if not self._running.is_set():
                    try:
                        self.root.quit()
                    except Exception:
                        pass
                    return
                try:
                    with self.player._playlist_lock:
                        q = list(self.player._queue)
                    self.schedule_update(q)
                except Exception:
                    pass
                try:
                    self.root.after(1000, refresh_loop)
                except Exception:
                    pass

            # Initial fill
            try:
                with self.player._playlist_lock:
                    q = list(self.player._queue)
                self.schedule_update(q)
            except Exception:
                pass

            self.root.protocol('WM_DELETE_WINDOW', self.stop)
            self.root.after(1000, refresh_loop)
            # start scrolling loop
            try:
                self.root.after(1500, scroll_step)
            except Exception:
                pass
            self.root.mainloop()
        except Exception:
            return

def main():
    """Main entry point"""
    print("=" * 50)
    print("🎶 Random Playlist Player")
    print("   YouTube + Spotify Edition")
    print("=" * 50)
    
    # Get configuration from environment or prompt user
    youtube_url = os.getenv('YOUTUBE_PLAYLIST_URL', '')
    spotify_url = os.getenv('SPOTIFY_PLAYLIST_URL', '')
    spotify_client_id = os.getenv('SPOTIPY_CLIENT_ID', '')
    spotify_client_secret = os.getenv('SPOTIPY_CLIENT_SECRET', '')
    
    # Prompt for URLs if not in environment
    if not youtube_url:
        youtube_url = input("\nEnter YouTube playlist URL (or press Enter to skip): ").strip()
    
    if not spotify_url:
        spotify_url = input("Enter Spotify playlist URL (or press Enter to skip): ").strip()
    
    if spotify_url and not spotify_client_id:
        print("\n⚠️  Spotify requires API credentials.")
        print("   Get them from: https://developer.spotify.com/dashboard")
        spotify_client_id = input("Enter Spotify Client ID: ").strip()
        spotify_client_secret = input("Enter Spotify Client Secret: ").strip()
    
    if not youtube_url and not spotify_url:
        print("\n❌ No playlist URLs provided. Exiting.")
        return
    
    # Initialize player
    player = RandomPlayer()
    player.load_playlists(youtube_url, spotify_url, spotify_client_id, spotify_client_secret)
    
    if not player.all_tracks:
        print("\n❌ No tracks loaded. Please check your playlist URLs.")
        return
    
    # Start auto-refresh
    player.start_auto_refresh()
    # Start Next Up GUI (replaces next_up.txt)
    try:
        player.start_menu_window()
    except Exception:
        pass
    
    # Generate and display QR codes for playlist links
    if youtube_url:
        print("\n📱 YouTube Playlist QR Code:")
        qr = qrcode.QRCode(version=1, box_size=1, border=1)
        qr.add_data(youtube_url)
        qr.make(fit=True)
        qr.print_ascii()
    
    if spotify_url:
        print("\n📱 Spotify Playlist QR Code:")
        qr = qrcode.QRCode(version=1, box_size=1, border=1)
        qr.add_data(spotify_url)
        qr.make(fit=True)
        qr.print_ascii()
    
    # Main loop
    while True:
        # Always display QR codes and commands
        spotify_lines = get_qr_lines(spotify_url) if spotify_url else []
        youtube_lines = get_qr_lines(youtube_url) if youtube_url else []
        
        if spotify_lines and youtube_lines:
            print("\n📱 Spotify Playlist QR Code    📱 YouTube Playlist QR Code")
            max_len = max(len(spotify_lines), len(youtube_lines))
            for i in range(max_len):
                s_line = spotify_lines[i] if i < len(spotify_lines) else ''
                y_line = youtube_lines[i] if i < len(youtube_lines) else ''
                print(f"{s_line}    {y_line}")
        elif spotify_lines:
            print("\n📱 Spotify Playlist QR Code:")
            for line in spotify_lines:
                print(line)
        elif youtube_lines:
            print("\n📱 YouTube Playlist QR Code:")
            for line in youtube_lines:
                print(line)
        

        if player._queue:
            next_track = player._queue[0]
            print(f"  Next up: {next_track.title} by {next_track.artist} ")
            player.update_menu_file()
        print("=" * 50)
        
        choice = input("\n> ").strip().lower()
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(player._queue):
                track = player._queue.pop(idx)
                player._queue.insert(0, track)
                print(f"Moved '{track.title}' to front of queue.")
                player.update_menu_file()
            else:
                print("Invalid queue index. Use 'p' to see queue.")
        elif choice == 'q':
            player.stop_current(wait_after=False)
            player.stop_auto_refresh()
            # Stop NextUp GUI if running
            try:
                player.stop_menu_window()
            except Exception:
                pass
            print("\n👋 Goodbye!")
            break
        elif choice == 'x':
            player.stop_current(wait_after=False)
        elif choice == 'y':
            player.play_random_from_platform('youtube')
        elif choice == 'vr':
            player.perform_vr_sequence()
        elif choice == 'adder':
            player.toggle_show_adder_menu()
        elif choice == 'shuffle':
            random.shuffle(player._queue)
            print("Queue shuffled.")
            player.update_menu_file()
        elif choice == 'p':
            if player._queue:
                print("\n📋 Queue:")
                for i, track in enumerate(player._queue, 1):
                    print(f"  {i}. {track.title} by {track.artist} ({track.platform.upper()})")
            else:
                print("\n📋 Queue is empty.")
        else:
            player.play_random()


if __name__ == "__main__":
    main()
