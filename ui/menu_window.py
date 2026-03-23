"""Tkinter Next Up window for playlist player."""

import os
import random
import threading
from typing import Optional

import pyautogui


class Menu:
	"""A small Tkinter window that displays the upcoming queue and refreshes automatically."""

	def __init__(self, player: "RandomPlayer"):
		self.player = player
		self._thread: Optional[threading.Thread] = None
		self.root = None
		self._running = threading.Event()
		self._top_lbl = None
		self._listbox = None

	def start(self):
		if self._thread and self._thread.is_alive():
			return
		self._running.set()
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	def stop(self):
		self._running.clear()
		if self.root:
			self.root.quit()


	def schedule_update(self, queue_snapshot: list):
		"""Schedule an immediate UI update from any thread.

		Displays the first track in a fixed label and fills the queue view
		with the remaining entries.
		"""
		try:
			if not self.root:
				return
			def _update():
				top_lbl = getattr(self, "_top_lbl", None)
				tree = getattr(self, "_tree", None)
				listbox = getattr(self, "_listbox", None)
				# require top area and at least one of tree/listbox
				if top_lbl is None or (tree is None and listbox is None):
					return
				# Update top "Next up"
				if not queue_snapshot:
					top_lbl.config(text="Next up: (none)")
					listbox.delete(0, "end")
					return

				first = queue_snapshot[0]
				title_short = (first.title[:30]) if first.title else ""
				artist_short = (first.artist[:20]) if first.artist else ""

				# Support both Label and Text widgets for the top area.
				# Build top text; if adder display is enabled, add second line with adder
				top_text = f"Next: {title_short} — {artist_short}"
				if getattr(self.player, "_show_adder_nextup", False):
					ab = getattr(first, "added_by_name", None) or getattr(first, "added_by_id", None)
					if ab:
						try:
							ab_short = ab[:30] if isinstance(ab, str) else str(ab)
						except Exception:
							ab_short = str(ab)
						top_text = top_text + "\nAdded by: " + ab_short

				# If it's a Text widget, replace contents and keep it readonly
				if hasattr(top_lbl, "delete") and hasattr(top_lbl, "insert"):
						top_lbl.config(state="normal")
						top_lbl.delete("1.0", "end")
						top_lbl.insert("1.0", top_text)
						top_lbl.config(state="disabled")
				else:
					top_lbl.config(text=top_text)
					top_lbl.config(text=f"Next: {title_short} — {artist_short}")

				# Prepare list of tracks
				rest = []
				# Enumerate tracks and show 1-based ordinals (1,2,3,...).
				# The top area still highlights the first item, but the
				# table/list will include it as well.
				for idx, t in enumerate(queue_snapshot, start=1):
					t_title = t.title[:30] if t.title else ""
					t_artist = t.artist[:20] if t.artist else ""

					# Fixed-width index (right-aligned 3 chars) with dot and a space
					prefix = f"{idx:>3}. "
					# Build fixed-width table columns for index, title, artist, optional adder
					if getattr(self.player, "_show_adder_nextup", False):
						ab = getattr(t, "added_by_name", None) or getattr(t, "added_by_id", None)
						ab_short = ""
						if ab:
							ab_short = (ab[:18]) if isinstance(ab, str) else str(ab)
						line = f"{prefix}{t_title:<30} {t_artist:<20} {ab_short:<18}"
					else:
						line = f"{prefix}{t_title:<30} {t_artist:<20}"
					rest.append(line)

				# Update header to reflect current adder toggle
				hdr = getattr(self, "_header_lbl", None)
				if hdr is not None:
					show_adder = getattr(self.player, "_show_adder_nextup", False)
					header_text = f"{'#':>3}. {'Title':30} {'Artist':20} {'Adder' if show_adder else ''}"
					hdr.config(text=header_text)

				# Update tree/listbox contents
				if tree is not None:
					# show/hide adder column based on toggle
					if getattr(self.player, "_show_adder_nextup", False):
						tree["displaycolumns"] = ("idx", "title", "artist", "adder")
					else:
						tree["displaycolumns"] = ("idx", "title", "artist")
					for ch in tree.get_children():
						tree.delete(ch)

					# Adjust column widths when adder column is toggled so the
					# visible columns reflow to sensible sizes.
					show_adder = getattr(self.player, "_show_adder_nextup", False)
					self._reflow_columns(getattr(self, "_tree", None), show_adder)

					# Insert rows into tree; rest already contains formatted lines but we also insert structured values if available
					for i, t in enumerate(queue_snapshot, start=1):
						title_short = (t.title[:30]) if t.title else ""
						artist_short = (t.artist[:20]) if t.artist else ""
						ab = getattr(t, "added_by_name", None) or getattr(t, "added_by_id", None)
						ab_short = ""
						if getattr(self.player, "_show_adder_nextup", False) and ab:
							ab_short = (ab[:18]) if isinstance(ab, str) else str(ab)

						try:
							tree.insert("", "end", iid=str(i), values=(i, title_short, artist_short, ab_short))
						except Exception:
							tree.insert("", "end", values=(i, title_short, artist_short, ab_short))
				else:
					listbox.delete(0, "end")
					for item in rest:
						listbox.insert("end", item)
			self.root.after(0, _update)
		except Exception:
			pass

	def _reflow_columns(self, tree, show_adder: bool):
		"""Set Treeview column widths as percentages of available width.

		`show_adder` controls whether the `adder` column is shown and sized.
		"""
		if tree is None:
			return

		total_w = tree.winfo_width() - 20
		if total_w < 50:
			total_w = self.root.winfo_width() if self.root is not None else 1080

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

		tree.column("idx", width=idx_w, anchor="e")
		tree.column("title", width=title_w, anchor="w")
		tree.column("artist", width=artist_w, anchor="w")
		if show_adder:
			adder_w = max(60, int(avail * adder_pct))
			tree.column("adder", width=adder_w, anchor="w")
		else:
			tree.column("adder", width=0)

	def _run(self):
		try:
			import tkinter as tk
			import tkinter.font as tkfont
			from tkinter import ttk
		except Exception as e:
			print(f"[Menu] Failed to import tkinter UI dependencies: {e}")
			return

		def _report(context: str, err: Exception):
			print(f"[Menu] {context}: {err}")

		try:
			self.root = tk.Tk()
			self.root.title("Next Up")
			self.root.geometry("1080x960")

			# Top area: always-visible "Next" label
			top_font = tkfont.Font(size=48, weight="bold")
			bg = self.root.cget("bg")

			top_txt = tk.Text(self.root, name="nextup_top", height=2, wrap="word", font=top_font, bd=0, relief="flat")
			top_txt.config(bg=bg)
			top_txt.config(state="disabled")
			top_txt.pack(fill="x", padx=8, pady=(8, 4))
			self._top_lbl = top_txt

			# Controls
			btn_font = tkfont.Font(size=18, weight="bold")
			btn_frame = tk.Frame(self.root)
			btn_frame.pack(fill="x", padx=8, pady=(4, 4))
			btn_frame2 = tk.Frame(self.root)
			btn_frame2.pack(fill="x", padx=8, pady=(0, 8))

			frame = tk.Frame(self.root)
			frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

			list_font = tkfont.Font(family="Consolas", size=24)

			def _shuffle_queue():
				try:
					with self.player._playlist_lock:
						random.shuffle(self.player._queue)
					print("Queue shuffled (NextUp window)")
					self.player.update_menu_file()
				except Exception as e:
					_report("Shuffle queue failed", e)

			def _next_track():
				try:
					self.player._play_next_from_queue()
					self.player.update_menu_file()
				except Exception as e:
					_report("Play next track failed", e)

			def _pause_playback():
				try:
					res = self.player.pause_playback()
					print(f"Playback pause/unpause attempted: {res}")
				except Exception as e:
					_report("Pause playback failed", e)

			def _reset_vr():
				try:
					bx = b_reset.winfo_rootx()
					by = b_reset.winfo_rooty()
					bw = b_reset.winfo_width()
					bh = b_reset.winfo_height()
					btn_center = (bx + bw // 2, by + bh // 2)
					self.player.perform_vr_reset()
					pyautogui.FAILSAFE = False
					pyautogui.moveTo(btn_center[0], btn_center[1])
					print("VR reset triggered from NextUp window")
				except Exception as e:
					_report("VR reset failed", e)

			def _refresh_tab():
				try:
					bx = b_refresh.winfo_rootx()
					by = b_refresh.winfo_rooty()
					bw = b_refresh.winfo_width()
					bh = b_refresh.winfo_height()
					btn_center = (bx + bw // 2, by + bh // 2)
					res = self.player.refresh_current_tab()
					print(f"Refresh attempted: {res}")
					pyautogui.FAILSAFE = False
					pyautogui.moveTo(btn_center[0], btn_center[1])
				except Exception as e:
					_report("Refresh tab failed", e)

			def _toggle_adder():
				try:
					self.player.toggle_show_adder_menu()
					self.player.update_menu_file()
				except Exception as e:
					_report("Toggle adder failed", e)

			def _toggle_qr_display():
				try:
					if getattr(self, "_qr_frame", None) and getattr(self, "_qr_visible", False):
						self._qr_frame.pack_forget()
						self._qr_visible = False
						return

					import glob
					from PIL import Image, ImageTk

					base_dir = os.path.dirname(os.path.dirname(__file__))
					qr_dir = os.path.join(base_dir, "qrcodes")
					png_paths = sorted(glob.glob(os.path.join(qr_dir, "*.png")))
					if not png_paths:
						return

					if not getattr(self, "_qr_frame", None):
						self._qr_frame = tk.Frame(self.root)
					else:
						for ch in self._qr_frame.winfo_children():
							ch.destroy()

					self._qr_images_refs = []
					for p in png_paths:
						sub = tk.Frame(self._qr_frame)
						sub.pack(side="left", padx=6, pady=4)
						img = Image.open(p)
						target_h = 400
						w = int(img.width * (target_h / img.height)) if img.height else img.width
						img = img.resize((w, target_h), Image.LANCZOS)
						photo = ImageTk.PhotoImage(img)
						lbl = tk.Label(sub, image=photo, bd=0)
						lbl.pack(side="top")
						name = os.path.splitext(os.path.basename(p))[0]
						cap = tk.Label(sub, text=name, anchor="center")
						cap.pack(side="top", pady=(4, 0))
						self._qr_images_refs.append(photo)

					self._qr_frame.pack(fill="x", padx=8, pady=(4, 4), before=frame)
					self._qr_visible = True
				except Exception as e:
					_report("Toggle QR display failed", e)

			def _open_voice_mix_slider():
				try:
					opened = self.player.open_demucs_mix_slider()
					print(f"Voice mix slider opened: {opened}")
				except Exception as e:
					_report("Open voice mix slider failed", e)

			def _quit_app():
				try:
					self.player.stop_current(wait_after=False)
					self.player.stop_auto_refresh()
					self.player.stop_menu_window()
					import os as _os
					_os._exit(0)
				except Exception as e:
					_report("Quit app failed", e)
					self.root.quit()

			def _vr_on():
				try:
					bx = b_vron.winfo_rootx()
					by = b_vron.winfo_rooty()
					bw = b_vron.winfo_width()
					bh = b_vron.winfo_height()
					btn_center = (bx + bw // 2, by + bh // 2)
					self.player.perform_vr_on()
					pyautogui.FAILSAFE = False
					pyautogui.moveTo(btn_center[0], btn_center[1])
				except Exception as e:
					_report("VR ON failed", e)

			def _vroff():
				try:
					bx = b_vroff.winfo_rootx()
					by = b_vroff.winfo_rooty()
					bw = b_vroff.winfo_width()
					bh = b_vroff.winfo_height()
					btn_center = (bx + bw // 2, by + bh // 2)
					self.player.perform_vr_off()
					pyautogui.FAILSAFE = False
					pyautogui.moveTo(btn_center[0], btn_center[1])
				except Exception as e:
					_report("VR OFF failed", e)

			def _calibrate_vr():
				try:
					dlg = tk.Toplevel(self.root)
					dlg.title("Calibrate VR")
					dlg.geometry("560x220")
					dlg.transient(self.root)

					status = tk.Label(
						dlg,
						text="Press Start to begin. Hover over each point and press Enter to capture. Before starting calibration open a youtube video and fullscreen the browser window via f11 and then fullscreen the video via F.",
						wraplength=520,
						justify="left",
					)
					status.pack(fill="x", padx=8, pady=(8, 4))

					pos_lbl = tk.Label(dlg, text="Current mouse: (x, y)")
					pos_lbl.pack(anchor="w", padx=8)
					btn_frame_cal = tk.Frame(dlg)
					btn_frame_cal.pack(fill="x", pady=8, padx=8)

					capturing = {"active": False}
					steps = []
					captures = {}

					def update_pos():
						if not dlg.winfo_exists():
							return
						p = pyautogui.position()
						pos_lbl.config(text=f"Current mouse: ({p[0]}, {p[1]})")
						dlg.after(100 if capturing.get("active") else 300, update_pos)

					def start_capture():
						seq = ["base1", "base2", "spotify_last", "youtube_last", "youtube_extra"]
						steps.clear()
						for s in seq:
							steps.append(s)
						captures.clear()
						capturing["active"] = True
						status.config(text=f"Step 1/{len(steps)}: Hover on Karaoke Monster Extension and press Enter")
						dlg.focus_force()
						dlg.bind("<Return>", on_enter)
						update_pos()

					def on_enter(event=None):
						if not capturing.get("active"):
							return
						p = pyautogui.position()
						idx = len(captures)
						step_name = steps[idx]
						captures[step_name] = (p[0], p[1])
						if len(captures) >= len(steps):
							capturing["active"] = False
							dlg.unbind("<Return>")
							if "base1" in captures and "base2" in captures:
								self.player._vr_points["base"] = [captures["base1"], captures["base2"]]
							if "spotify_last" in captures:
								self.player._vr_points["spotify_last"] = captures["spotify_last"]
							if "youtube_last" in captures:
								self.player._vr_points["youtube_last"] = captures["youtube_last"]
							if "youtube_extra" in captures:
								self.player._vr_points["youtube_extra"] = captures["youtube_extra"]
							self.player._save_vr_points()
							print(f"VR calibration saved: {self.player._vr_points}")
							status.config(text="Calibration complete. You can close this window.")
							return
						next_idx = len(captures)
						status.config(text=f"Step {next_idx+1}/{len(steps)}: hover next point and press Enter")

					def cancel():
						capturing["active"] = False
						dlg.unbind("<Return>")
						dlg.destroy()

					b_start = tk.Button(btn_frame_cal, text="Start", command=start_capture)
					b_cancel = tk.Button(btn_frame_cal, text="Cancel", command=cancel)
					b_start.pack(side="left", padx=8)
					b_cancel.pack(side="left", padx=8)
					dlg.after(100, update_pos)
					dlg.focus_force()
				except Exception as e:
					_report("Calibrate VR dialog failed", e)

			b_shuffle = tk.Button(btn_frame, text="Shuffle", command=_shuffle_queue, font=btn_font, padx=16, pady=8)
			b_next = tk.Button(btn_frame, text="Next", command=_next_track, font=btn_font, padx=16, pady=8)
			b_pause = tk.Button(btn_frame, text="Pause", command=_pause_playback, font=btn_font, padx=12, pady=6)
			b_refresh = tk.Button(btn_frame, text="Reset Tab", command=_refresh_tab, font=btn_font, padx=12, pady=6)
			b_reset = tk.Button(btn_frame2, text="Reset VR", command=_reset_vr, font=btn_font, padx=16, pady=8)
			b_vron = tk.Button(btn_frame2, text="VR ON", command=_vr_on, font=btn_font, padx=16, pady=8)
			b_vroff = tk.Button(btn_frame2, text="VR OFF", command=_vroff, font=btn_font, padx=16, pady=8)
			b_calibrate = tk.Button(btn_frame2, text="Calibrate VR", command=_calibrate_vr, font=btn_font, padx=12, pady=6)
			b_voice_mix = tk.Button(btn_frame2, text="Voice Mix", command=_open_voice_mix_slider, font=btn_font, padx=12, pady=6)
			b_adder = tk.Button(btn_frame, text="Adder", command=_toggle_adder, font=btn_font, padx=12, pady=6)
			b_qr = tk.Button(btn_frame, text="QR", command=_toggle_qr_display, font=btn_font, padx=10, pady=4)
			b_quit = tk.Button(btn_frame, text="Quit", command=_quit_app, font=btn_font, padx=12, pady=6)

			b_shuffle.pack(side="left", padx=8, pady=4)
			b_next.pack(side="left", padx=8, pady=4)
			b_pause.pack(side="left", padx=8, pady=4)
			b_refresh.pack(side="left", padx=8, pady=4)
			b_reset.pack(in_=btn_frame2, side="left", padx=8, pady=4)
			b_vron.pack(in_=btn_frame2, side="left", padx=8, pady=4)
			b_vroff.pack(in_=btn_frame2, side="left", padx=8, pady=4)
			b_calibrate.pack(in_=btn_frame2, side="left", padx=8, pady=4)
			b_voice_mix.pack(in_=btn_frame2, side="left", padx=8, pady=4)
			b_adder.pack(side="right", padx=8, pady=4)
			b_qr.pack(side="right", padx=8, pady=4)
			b_quit.pack(side="right", padx=8, pady=4)

			# Scrolling table for the rest (Treeview for columns)
			style = ttk.Style()
			row_h = list_font.metrics("linespace")
			style.configure("Treeview", font=list_font, rowheight=row_h)

			tree = ttk.Treeview(frame, columns=("idx", "title", "artist", "adder"), show="headings", height=32)
			tree.heading("idx", text="#")
			tree.heading("title", text="Title")
			tree.heading("artist", text="Artist")
			tree.heading("adder", text="Adder")
			self._reflow_columns(tree, getattr(self.player, "_show_adder_nextup", False))

			vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
			tree.configure(yscrollcommand=vsb.set)
			tree.pack(side="left", fill="both", expand=True)
			vsb.pack(side="right", fill="y")
			self._tree = tree
			frame.bind("<Configure>", lambda e: self._reflow_columns(tree, getattr(self.player, "_show_adder_nextup", False)))
			self.root.bind("<Configure>", lambda e: self._reflow_columns(tree, getattr(self.player, "_show_adder_nextup", False)))

			def _on_tree_button_press(event):
				try:
					item = tree.identify_row(event.y)
					if not item:
						return
					self._dragging = True
					self._drag_iid = item
					tree.selection_set(item)
				except Exception as e:
					_report("Tree button press handler failed", e)

			def _on_tree_motion(event):
				try:
					if not getattr(self, "_dragging", False):
						return
					over = tree.identify_row(event.y)
					if over:
						tree.selection_set(over)
				except Exception as e:
					_report("Tree drag motion handler failed", e)

			def _on_tree_button_release(event):
				try:
					if not getattr(self, "_dragging", False):
						return
					from_iid = getattr(self, "_drag_iid", None)
					self._dragging = False
					self._drag_iid = None
					if from_iid is None:
						return
					target = tree.identify_row(event.y)
					with self.player._playlist_lock:
						from_idx = int(from_iid) - 1
						to_idx = int(target) - 1 if target else None
						if from_idx < 0 or from_idx >= len(self.player._queue):
							return
						item = self.player._queue.pop(from_idx)
						if to_idx is None:
							self.player._queue.append(item)
						else:
							if from_idx < to_idx:
								to_idx = max(0, to_idx)
							insert_at = min(max(0, to_idx), len(self.player._queue))
							self.player._queue.insert(insert_at, item)
					self.player.update_menu_file()
				except Exception as e:
					_report("Tree drag release handler failed", e)

			def _on_tree_double_click(event):
				try:
					item = tree.identify_row(event.y)
					if not item:
						return
					idx = int(item) - 1
					with self.player._playlist_lock:
						if idx < 0 or idx >= len(self.player._queue):
							return
						track = self.player._queue.pop(idx)
					self.player.play_track(track)
					self.player.update_menu_file()
				except Exception as e:
					_report("Tree double-click handler failed", e)

			tree.bind("<ButtonPress-1>", _on_tree_button_press)
			tree.bind("<B1-Motion>", _on_tree_motion)
			tree.bind("<ButtonRelease-1>", _on_tree_button_release)
			tree.bind("<Double-1>", _on_tree_double_click)

			def refresh_loop():
				try:
					if not self._running.is_set():
						self.root.quit()
						return
					with self.player._playlist_lock:
						q = list(self.player._queue)
					self.schedule_update(q)
					self.root.after(1000, refresh_loop)
				except Exception as e:
					_report("Refresh loop failed", e)

			with self.player._playlist_lock:
				q = list(self.player._queue)
			self.schedule_update(q)

			self.root.protocol("WM_DELETE_WINDOW", self.stop)
			self.root.after(1000, refresh_loop)
			self.root.mainloop()
		except Exception as e:
			print(f"[Menu] UI thread crashed: {e}")
			return
