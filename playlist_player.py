"""Random Playlist Player launcher."""

import io
import os
import random
from contextlib import redirect_stdout

import qrcode
from dotenv import load_dotenv

from backend.player_core_desktop import RandomPlayer as PlayerCore

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
    lines = ascii_str.split("\n")
    return lines


def main():
    """Main entry point."""
    print("=" * 50)
    print("🎶 Random Playlist Player")
    print("   YouTube + Spotify Edition")
    print("=" * 50)

    # Get configuration from environment or prompt user
    youtube_url = os.getenv("YOUTUBE_PLAYLIST_URL", "")
    spotify_url = os.getenv("SPOTIFY_PLAYLIST_URL", "")
    spotify_client_id = os.getenv("SPOTIPY_CLIENT_ID", "")
    spotify_client_secret = os.getenv("SPOTIPY_CLIENT_SECRET", "")

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

    # Initialize player through backend module boundary
    player = PlayerCore()
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
                s_line = spotify_lines[i] if i < len(spotify_lines) else ""
                y_line = youtube_lines[i] if i < len(youtube_lines) else ""
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
        elif choice == "q":
            player.stop_current(wait_after=False)
            player.stop_auto_refresh()
            # Stop NextUp GUI if running
            try:
                player.stop_menu_window()
            except Exception:
                pass
            print("\n👋 Goodbye!")
            break
        elif choice == "x":
            player.stop_current(wait_after=False)
        elif choice == "y":
            player.play_random_from_platform("youtube")
        elif choice == "vr":
            player.perform_vr_sequence()
        elif choice == "adder":
            player.toggle_show_adder_menu()
        elif choice == "shuffle":
            random.shuffle(player._queue)
            print("Queue shuffled.")
            player.update_menu_file()
        elif choice == "p":
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
