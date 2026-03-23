import argparse
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Union

try:
    import tkinter as tk
except Exception:
    tk = None

import numpy as np
import sounddevice as sd


DeviceRef = Union[int, str, None]


@dataclass
class Stats:
    started_at: float
    captured_blocks: int = 0
    played_blocks: int = 0
    dropped_blocks: int = 0
    underflow_blocks: int = 0
    max_queue_depth: int = 0
    input_peak: float = 0.0
    output_peak: float = 0.0
    processed_blocks: int = 0
    bypass_blocks: int = 0
    processor_errors: int = 0
    processor_process_ms_total: float = 0.0
    processor_process_ms_peak: float = 0.0
    vocals_peak: float = 0.0
    instrumental_peak: float = 0.0


class StemSeparator:
    def separate(self, block: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError


class DemucsVocalsInstSeparator(StemSeparator):
    """Real separator backend using repository Demucs models."""

    def __init__(
        self,
        model_name: str = "htdemucs",
        device: str = "cpu",
        segment_sec: Optional[float] = None,
        overlap: float = 0.25,
        loudness_match: bool = True,
    ) -> None:
        try:
            import torch
            from demucs.apply import apply_model
            from demucs.pretrained import get_model
        except Exception as exc:
            raise RuntimeError(
                "Unable to import Demucs runtime. Ensure torch and demucs dependencies are available."
            ) from exc

        self.torch = torch
        self.apply_model = apply_model
        self.device = torch.device(device)
        self.overlap = overlap
        self.loudness_match = bool(loudness_match)

        self.model = get_model(name=model_name)
        self.model.to(self.device)
        self.model.eval()

        if hasattr(self.model, "segment") and segment_sec is not None and segment_sec > 0:
            self.model.segment = float(segment_sec)

        self.sources = list(getattr(self.model, "sources", []))
        if "vocals" not in self.sources:
            raise RuntimeError(f"Demucs model '{model_name}' does not expose a 'vocals' source.")

        self.vocal_index = self.sources.index("vocals")
        self.inst_indices = [idx for idx in range(len(self.sources)) if idx != self.vocal_index]

    def separate(self, block: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if block.ndim != 2:
            raise RuntimeError(f"Expected 2D block [samples, channels], got shape {block.shape}")

        mix = np.copy(block).astype(np.float32)
        tensor_mix = self.torch.from_numpy(mix.T).unsqueeze(0).to(self.device)

        with self.torch.no_grad():
            estimates = self.apply_model(
                self.model,
                tensor_mix,
                shifts=0,
                split=True,
                overlap=self.overlap,
                progress=False,
                device=self.device,
                num_workers=0,
            )

        vocals_t = estimates[:, self.vocal_index, :, :]
        if self.inst_indices:
            inst_t = estimates[:, self.inst_indices, :, :].sum(dim=1)
        else:
            inst_t = self.torch.zeros_like(vocals_t)

        vocals = vocals_t.squeeze(0).detach().cpu().numpy().T.astype(np.float32)
        instrumental = inst_t.squeeze(0).detach().cpu().numpy().T.astype(np.float32)

        if self.loudness_match:
            eps = 1e-8
            out_mix = vocals + instrumental
            in_rms = float(np.sqrt(np.mean(np.square(mix), dtype=np.float64)))
            out_rms = float(np.sqrt(np.mean(np.square(out_mix), dtype=np.float64)))
            if out_rms > eps and in_rms > eps:
                gain = float(np.clip(in_rms / out_rms, 0.5, 6.0))
                vocals *= gain
                instrumental *= gain

        vocals = np.clip(vocals, -1.0, 1.0).astype(np.float32)
        instrumental = np.clip(instrumental, -1.0, 1.0).astype(np.float32)
        return vocals, instrumental


class StemMixController:
    def __init__(self, vocal_mix: float = 0.5, mode: str = "add-vocals") -> None:
        self._lock = threading.Lock()
        self.mode = mode
        self._vocal_mix = 0.5
        self.set_vocal_mix(vocal_mix)

    def set_vocal_mix(self, value: float) -> None:
        with self._lock:
            self._vocal_mix = min(1.0, max(0.0, float(value)))

    def get_vocal_mix(self) -> float:
        with self._lock:
            return self._vocal_mix

    def get_gains(self) -> Tuple[float, float]:
        v = self.get_vocal_mix()
        # Keep instrumental at full level; slider only adds/removes vocals.
        return v, 1.0


class MixSliderUI:
    def __init__(self, mix_controller: StemMixController) -> None:
        self.mix_controller = mix_controller
        self.thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if tk is None:
            print("[mix-ui] tkinter is unavailable; mix UI disabled.", flush=True)
            return False

        self.thread = threading.Thread(target=self._run_ui, daemon=True)
        self.thread.start()
        return True

    def _run_ui(self) -> None:
        try:
            root = tk.Tk()
            root.title("Live Stem Mix (Demucs)")
            root.resizable(False, False)

            title = tk.Label(root, text="Instrumental -> Mixed")
            title.pack(pady=(10, 0))

            value_label = tk.Label(root, text="")
            value_label.pack(pady=(6, 0))

            def on_change(value: str) -> None:
                vocal_mix = float(value) / 100.0
                self.mix_controller.set_vocal_mix(vocal_mix)
                v_gain, i_gain = self.mix_controller.get_gains()
                value_label.config(text=f"Vocals add: {v_gain:.2f}  Instrumental: {i_gain:.2f}")

            initial = int(self.mix_controller.get_vocal_mix() * 100.0)
            slider = tk.Scale(
                root,
                from_=0,
                to=100,
                orient=tk.HORIZONTAL,
                command=on_change,
                length=320,
                label="0 = Instrumental only, 100 = Mixed (instrumental + vocals)",
            )
            slider.set(initial)
            slider.pack(pady=(4, 8))

            # Size the window to fit packed widgets so controls are visible immediately.
            root.update_idletasks()
            req_w = root.winfo_reqwidth()
            req_h = root.winfo_reqheight()
            root.geometry(f"{req_w}x{req_h}")

            on_change(str(initial))
            root.mainloop()
        except Exception as exc:
            print(f"[mix-ui] failed: {exc}", flush=True)


class LiveLoopbackHarness:
    def __init__(
        self,
        samplerate: int,
        channels: int,
        blocksize: int,
        latency_buffers: int,
        gain: float,
        input_device: DeviceRef,
        output_device: DeviceRef,
        separator: Optional[StemSeparator],
        mix_controller: Optional[StemMixController] = None,
        bypass_high_watermark: int = 6,
        bypass_low_watermark: int = 3,
        bypass_enabled: bool = True,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.blocksize = blocksize
        self.gain = gain
        self.input_device = input_device
        self.output_device = output_device
        self.capture_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(2, latency_buffers))
        self.playback_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(2, latency_buffers))
        self.stats = Stats(started_at=time.perf_counter())
        self.stop_event = threading.Event()
        self.separator = separator
        self.separator_enabled = self.separator is not None
        self.mix_controller = mix_controller or StemMixController(vocal_mix=0.5)
        self.processor_thread: Optional[threading.Thread] = None
        self.processor_bypass_active = False
        self.bypass_enabled = bypass_enabled
        self.bypass_high_watermark = max(2, bypass_high_watermark)
        self.bypass_low_watermark = max(0, min(bypass_low_watermark, self.bypass_high_watermark - 1))

    def _enqueue_latest(self, q: queue.Queue[np.ndarray], block: np.ndarray) -> None:
        if q.full():
            try:
                q.get_nowait()
                self.stats.dropped_blocks += 1
            except queue.Empty:
                pass

        try:
            q.put_nowait(block)
        except queue.Full:
            self.stats.dropped_blocks += 1

    def _capture_callback(self, indata: np.ndarray, _frames: int, _time_info, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[capture] status: {status}", flush=True)

        block = np.copy(indata)
        self.stats.captured_blocks += 1
        current_peak = float(np.max(np.abs(block))) if block.size else 0.0
        if current_peak > self.stats.input_peak:
            self.stats.input_peak = current_peak

        if self.separator_enabled:
            self._enqueue_latest(self.capture_queue, block)
        else:
            self._enqueue_latest(self.playback_queue, block)

        depth = self.capture_queue.qsize() + self.playback_queue.qsize()
        if depth > self.stats.max_queue_depth:
            self.stats.max_queue_depth = depth

    def _processor_loop(self) -> None:
        while not self.stop_event.is_set() or not self.capture_queue.empty():
            try:
                block = self.capture_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            in_depth = self.capture_queue.qsize()
            if self.bypass_enabled:
                if self.processor_bypass_active and in_depth <= self.bypass_low_watermark:
                    self.processor_bypass_active = False
                elif (not self.processor_bypass_active) and in_depth >= self.bypass_high_watermark:
                    self.processor_bypass_active = True
            else:
                self.processor_bypass_active = False

            out_block = block
            slider_passthrough_active = self.mix_controller.get_vocal_mix() >= 0.999

            if self.processor_bypass_active or slider_passthrough_active:
                self.stats.bypass_blocks += 1
            else:
                started = time.perf_counter()
                try:
                    if self.separator is not None:
                        vocals, instrumental = self.separator.separate(block)
                        v_peak = float(np.max(np.abs(vocals))) if vocals.size else 0.0
                        i_peak = float(np.max(np.abs(instrumental))) if instrumental.size else 0.0
                        if v_peak > self.stats.vocals_peak:
                            self.stats.vocals_peak = v_peak
                        if i_peak > self.stats.instrumental_peak:
                            self.stats.instrumental_peak = i_peak

                        vocal_gain, instrumental_gain = self.mix_controller.get_gains()
                        out_block = np.clip(
                            vocals * vocal_gain + instrumental * instrumental_gain,
                            -1.0,
                            1.0,
                        ).astype(np.float32)
                except Exception as exc:
                    self.stats.processor_errors += 1
                    self.processor_bypass_active = True
                    out_block = block
                    print(f"[processor] error: {exc}", flush=True)

                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self.stats.processed_blocks += 1
                self.stats.processor_process_ms_total += elapsed_ms
                if elapsed_ms > self.stats.processor_process_ms_peak:
                    self.stats.processor_process_ms_peak = elapsed_ms

            if out_block.dtype != np.float32:
                out_block = out_block.astype(np.float32)

            self._enqueue_latest(self.playback_queue, out_block)

    def _playback_callback(self, outdata: np.ndarray, frames: int, _time_info, status: sd.CallbackFlags) -> None:
        if status:
            print(f"[playback] status: {status}", flush=True)

        try:
            block = self.playback_queue.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            self.stats.underflow_blocks += 1
            return

        if block.shape[0] != frames:
            outdata.fill(0)
            copy_frames = min(block.shape[0], frames)
            outdata[:copy_frames] = block[:copy_frames]
            self.stats.underflow_blocks += 1
            output_peak = float(np.max(np.abs(outdata))) if outdata.size else 0.0
            if output_peak > self.stats.output_peak:
                self.stats.output_peak = output_peak
            self.stats.played_blocks += 1
            return

        if self.gain != 1.0:
            outdata[:] = np.clip(block * self.gain, -1.0, 1.0)
        else:
            outdata[:] = block

        self.stats.played_blocks += 1
        output_peak = float(np.max(np.abs(outdata))) if outdata.size else 0.0
        if output_peak > self.stats.output_peak:
            self.stats.output_peak = output_peak

    def run(self, duration: float, verbose_interval: float) -> None:
        if self.separator_enabled:
            self.processor_thread = threading.Thread(target=self._processor_loop, daemon=True)
            self.processor_thread.start()

        input_kwargs = {
            "samplerate": self.samplerate,
            "device": self.input_device,
            "channels": self.channels,
            "blocksize": self.blocksize,
            "dtype": "float32",
            "callback": self._capture_callback,
        }

        extra_settings = create_wasapi_input_settings()
        if extra_settings is not None:
            input_kwargs["extra_settings"] = extra_settings

        try:
            with sd.InputStream(**input_kwargs), sd.OutputStream(
                samplerate=self.samplerate,
                device=self.output_device,
                channels=self.channels,
                blocksize=self.blocksize,
                dtype="float32",
                callback=self._playback_callback,
            ):
                self._monitor_loop(duration=duration, verbose_interval=verbose_interval)
        finally:
            self.stop_event.set()
            if self.processor_thread is not None:
                self.processor_thread.join(timeout=1.0)

    def _monitor_loop(self, duration: float, verbose_interval: float) -> None:
        next_log = time.perf_counter() + verbose_interval
        started = time.perf_counter()

        while not self.stop_event.is_set():
            now = time.perf_counter()
            if duration > 0 and now - started >= duration:
                break

            if now >= next_log:
                print(self.format_runtime_stats(), flush=True)
                next_log = now + verbose_interval

            time.sleep(0.05)

    def format_runtime_stats(self) -> str:
        cap_depth = self.capture_queue.qsize()
        play_depth = self.playback_queue.qsize()
        q_depth = cap_depth + play_depth
        est_latency_ms = (q_depth * self.blocksize / self.samplerate) * 1000.0
        avg_process_ms = (
            self.stats.processor_process_ms_total / self.stats.processed_blocks
            if self.stats.processed_blocks
            else 0.0
        )

        processor_segment = ""
        if self.separator_enabled:
            vocal_gain, instrumental_gain = self.mix_controller.get_gains()
            total_mix_blocks = self.stats.processed_blocks + self.stats.bypass_blocks
            bypass_ratio = (self.stats.bypass_blocks / total_mix_blocks) if total_mix_blocks else 0.0
            bypass_note = ""
            if bypass_ratio >= 0.5:
                bypass_note = " [mix-limited: high bypass]"
            processor_segment = (
                f", proc={self.stats.processed_blocks}, bypass={self.stats.bypass_blocks}, "
                f"proc_err={self.stats.processor_errors}, proc_avg_ms={avg_process_ms:.2f}, "
                f"proc_peak_ms={self.stats.processor_process_ms_peak:.2f}, bypass_active={self.processor_bypass_active}, "
                f"bypass_pct={bypass_ratio * 100.0:.1f}%{bypass_note}, "
                f"voc_gain={vocal_gain:.2f}, inst_gain={instrumental_gain:.2f}, "
                f"voc_peak={self.stats.vocals_peak:.4f}, inst_peak={self.stats.instrumental_peak:.4f}"
            )

        return (
            f"[live] q={q_depth} blocks (capture={cap_depth}, playback={play_depth}), "
            f"est_buffer_latency={est_latency_ms:.1f} ms, "
            f"captured={self.stats.captured_blocks}, played={self.stats.played_blocks}, "
            f"drop={self.stats.dropped_blocks}, underflow={self.stats.underflow_blocks}, "
            f"in_peak={self.stats.input_peak:.4f}, out_peak={self.stats.output_peak:.4f}"
            f"{processor_segment}"
        )

    def summary(self) -> str:
        elapsed = time.perf_counter() - self.stats.started_at
        cap_depth = self.capture_queue.qsize()
        play_depth = self.playback_queue.qsize()
        q_depth = cap_depth + play_depth
        est_latency_ms = (q_depth * self.blocksize / self.samplerate) * 1000.0
        peak_latency_ms = (self.stats.max_queue_depth * self.blocksize / self.samplerate) * 1000.0
        avg_process_ms = (
            self.stats.processor_process_ms_total / self.stats.processed_blocks
            if self.stats.processed_blocks
            else 0.0
        )

        processor_lines = []
        if self.separator_enabled:
            vocal_gain, instrumental_gain = self.mix_controller.get_gains()
            total_mix_blocks = self.stats.processed_blocks + self.stats.bypass_blocks
            bypass_ratio = (self.stats.bypass_blocks / total_mix_blocks) if total_mix_blocks else 0.0
            processor_lines = [
                f"processor_processed_blocks: {self.stats.processed_blocks}",
                f"processor_bypass_blocks: {self.stats.bypass_blocks}",
                f"processor_bypass_pct: {bypass_ratio * 100.0:.1f}%",
                f"processor_errors: {self.stats.processor_errors}",
                f"processor_avg_ms: {avg_process_ms:.2f}",
                f"processor_peak_ms: {self.stats.processor_process_ms_peak:.2f}",
                f"processor_bypass_active_end: {self.processor_bypass_active}",
                f"bypass_high_watermark: {self.bypass_high_watermark}",
                f"bypass_low_watermark: {self.bypass_low_watermark}",
                f"vocal_gain_end: {vocal_gain:.2f}",
                f"instrumental_gain_end: {instrumental_gain:.2f}",
                f"vocals_peak: {self.stats.vocals_peak:.4f}",
                f"instrumental_peak: {self.stats.instrumental_peak:.4f}",
            ]

        return "\n".join(
            [
                "===== Live Harness Summary =====",
                f"elapsed_s: {elapsed:.2f}",
                f"samplerate: {self.samplerate}",
                f"channels: {self.channels}",
                f"blocksize: {self.blocksize}",
                f"captured_blocks: {self.stats.captured_blocks}",
                f"played_blocks: {self.stats.played_blocks}",
                f"dropped_blocks: {self.stats.dropped_blocks}",
                f"underflow_blocks: {self.stats.underflow_blocks}",
                f"max_queue_depth: {self.stats.max_queue_depth}",
                f"capture_queue_depth_end: {cap_depth}",
                f"playback_queue_depth_end: {play_depth}",
                f"input_peak: {self.stats.input_peak:.4f}",
                f"output_peak: {self.stats.output_peak:.4f}",
                f"estimated_buffer_latency_ms_now: {est_latency_ms:.1f}",
                f"estimated_buffer_latency_ms_peak: {peak_latency_ms:.1f}",
                *processor_lines,
                "================================",
            ]
        )


def create_separator(
    separator_name: str,
    demucs_model: str,
    demucs_device: str,
    demucs_segment_sec: Optional[float],
    demucs_overlap: float,
    demucs_loudness_match: bool,
) -> Optional[StemSeparator]:
    if separator_name == "none":
        return None
    if separator_name == "demucs-vocals-inst":
        return DemucsVocalsInstSeparator(
            model_name=demucs_model,
            device=demucs_device,
            segment_sec=demucs_segment_sec,
            overlap=demucs_overlap,
            loudness_match=demucs_loudness_match,
        )
    raise ValueError(f"Unknown separator: {separator_name}")


def parse_device_ref(value: Optional[str]) -> DeviceRef:
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return value


def parse_demucs_segment_arg(value: str) -> Optional[float]:
    value_norm = str(value).strip().lower()
    if value_norm in {"default", "def", "d"}:
        return None

    try:
        seg = float(value)
    except ValueError as exc:
        raise ValueError("--demucs-segment-sec must be 'Default' or a positive number.") from exc

    if seg <= 0:
        raise ValueError("--demucs-segment-sec numeric value must be > 0.")

    return seg


def create_wasapi_input_settings():
    # sounddevice versions differ: some support loopback=True, others do not.
    try:
        return sd.WasapiSettings(loopback=True)
    except TypeError:
        try:
            return sd.WasapiSettings()
        except Exception:
            return None
    except Exception:
        return None


def resolve_device_index(device_ref: DeviceRef) -> Optional[int]:
    if device_ref is None:
        return None

    if isinstance(device_ref, int):
        return device_ref

    devices = sd.query_devices()
    target = device_ref.lower()
    for idx, dev in enumerate(devices):
        if target in dev["name"].lower():
            return idx

    return None


def hostapi_name_for_device(device_index: int) -> str:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    return hostapis[devices[device_index]["hostapi"]]["name"]


def get_system_default_output_index() -> int:
    default_pair = sd.default.device
    if isinstance(default_pair, (list, tuple)) and len(default_pair) >= 2 and default_pair[1] is not None:
        return int(default_pair[1])

    for api in sd.query_hostapis():
        if api["name"] == "Windows WASAPI" and api["default_output_device"] != -1:
            return int(api["default_output_device"])

    raise RuntimeError("No system default output device found.")


def find_vb_cable_input(preferred_host_name: Optional[str] = None) -> Optional[int]:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    strict_name = "cable output (vb-audio virtual cable)"
    fuzzy_name = "cable output"

    strict_matches = []
    fuzzy_matches = []

    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] <= 0:
            continue

        dev_name = dev["name"].lower()
        host_name = hostapis[dev["hostapi"]]["name"]

        if strict_name in dev_name:
            strict_matches.append((idx, host_name))
        elif fuzzy_name in dev_name:
            fuzzy_matches.append((idx, host_name))

    def pick(matches):
        if not matches:
            return None

        if preferred_host_name:
            for idx, host_name in matches:
                if host_name == preferred_host_name:
                    return idx

        for idx, host_name in matches:
            if host_name == "Windows WASAPI":
                return idx

        return matches[0][0]

    chosen = pick(strict_matches)
    if chosen is not None:
        return chosen

    return pick(fuzzy_matches)


def find_wasapi_loopback_input(output_device_index: int) -> Optional[int]:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    output_name = devices[output_device_index]["name"].lower()

    wasapi_loopback_candidates = []

    for idx, dev in enumerate(devices):
        host_name = hostapis[dev["hostapi"]]["name"]
        if host_name != "Windows WASAPI":
            continue
        if dev["max_input_channels"] <= 0:
            continue
        if "loopback" not in dev["name"].lower():
            continue
        wasapi_loopback_candidates.append(idx)

    if not wasapi_loopback_candidates:
        return None

    for idx in wasapi_loopback_candidates:
        if output_name in devices[idx]["name"].lower():
            return idx

    return wasapi_loopback_candidates[0]


def list_devices() -> None:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    print("\n===== Audio Devices =====")
    for idx, dev in enumerate(devices):
        host = hostapis[dev["hostapi"]]["name"]
        loopback_tag = " [loopback]" if "loopback" in dev["name"].lower() else ""
        print(
            f"[{idx}] {dev['name']}{loopback_tag} | hostapi={host} | "
            f"in={dev['max_input_channels']} out={dev['max_output_channels']} "
            f"default_sr={dev['default_samplerate']}"
        )
    print("=========================\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Windows WASAPI loopback capture and playback harness (Demucs-only)."
    )
    parser.add_argument("--list-devices", action="store_true", help="Print available devices and exit.")
    parser.add_argument("--loopback-device", type=str, default=None, help="Input capture device index or name substring.")
    parser.add_argument("--output-device", type=str, default=None, help="Output playback device index or name substring.")
    parser.add_argument("--samplerate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=2)
    parser.add_argument("--blocksize", type=int, default=96000)
    parser.add_argument("--latency-buffers", type=int, default=8, help="Queue capacity in blocks.")
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument(
        "--separator",
        choices=["none", "demucs-vocals-inst"],
        default="demucs-vocals-inst",
        help="Separator mode.",
    )
    parser.add_argument(
        "--demucs-model",
        type=str,
        default="htdemucs",
        help="Demucs model name.",
    )
    parser.add_argument(
        "--demucs-device",
        choices=["cpu", "cuda"],
        default="cuda",
        help="Device for Demucs inference.",
    )
    parser.add_argument(
        "--demucs-segment-sec",
        type=str,
        default=2,
        help="Demucs segment size in seconds, or 'Default'.",
    )
    parser.add_argument(
        "--demucs-overlap",
        type=float,
        default=0.0,
        help="Demucs overlap ratio for split inference.",
    )
    parser.add_argument(
        "--demucs-no-loudness-match",
        action="store_true",
        help="Disable Demucs output loudness compensation to input level.",
    )
    parser.add_argument(
        "--vocal-mix",
        type=float,
        default=0.5,
        help="Initial vocal add level in [0.0, 1.0]. 0=instrumental only, 1=mixed (instrumental + vocals).",
    )
    parser.add_argument(
        "--mix-ui",
        action="store_true",
        help="Show a live slider UI to adjust vocal/instrumental mix in real time.",
    )
    parser.add_argument(
        "--bypass-high-watermark",
        type=int,
        default=6,
        help="Enable separator bypass when capture queue depth reaches this many blocks.",
    )
    parser.add_argument(
        "--bypass-low-watermark",
        type=int,
        default=3,
        help="Disable separator bypass once capture queue depth drops to this many blocks.",
    )
    parser.add_argument(
        "--no-bypass",
        action="store_true",
        help="Disable queue-pressure bypass and always apply separator output.",
    )
    parser.add_argument("--duration", type=float, default=0.0, help="Run duration in seconds. 0 means until Ctrl+C.")
    parser.add_argument("--verbose-interval", type=float, default=2.0)
    args = parser.parse_args()

    try:
        demucs_segment_sec = parse_demucs_segment_arg(args.demucs_segment_sec)
    except ValueError as exc:
        print(str(exc))
        return 1

    if sys.platform != "win32":
        print("This prototype currently targets Windows WASAPI loopback.")
        return 1

    if args.list_devices:
        list_devices()
        return 0

    if "Windows WASAPI" not in [api["name"] for api in sd.query_hostapis()]:
        print("Windows WASAPI host API was not found. Check your PortAudio backend.")
        return 1

    input_device = parse_device_ref(args.loopback_device)
    output_device = parse_device_ref(args.output_device)

    output_index = resolve_device_index(output_device)
    if output_index is None:
        output_index = get_system_default_output_index()

    preferred_input_host = hostapi_name_for_device(output_index)

    input_index = resolve_device_index(input_device)
    if input_index is None:
        input_index = find_vb_cable_input(preferred_host_name=preferred_input_host)

    if input_index is None:
        input_index = find_wasapi_loopback_input(output_index)

    if input_index is None:
        print("No VB-CABLE input or WASAPI loopback input found. Run with --list-devices and pass --loopback-device explicitly.")
        return 1

    input_device = input_index
    output_device = output_index

    devices = sd.query_devices()
    print(f"Selected input [{input_device}]: {devices[input_device]['name']} ({hostapi_name_for_device(input_device)})")
    print(f"Selected output [{output_device}]: {devices[output_device]['name']} ({hostapi_name_for_device(output_device)})")

    separator = create_separator(
        args.separator,
        demucs_model=args.demucs_model,
        demucs_device=args.demucs_device,
        demucs_segment_sec=demucs_segment_sec,
        demucs_overlap=args.demucs_overlap,
        demucs_loudness_match=not args.demucs_no_loudness_match,
    )
    mix_controller = StemMixController(vocal_mix=args.vocal_mix, mode="add-vocals")

    if separator is None:
        print("Separator: none")
    else:
        demucs_seg_display = args.demucs_segment_sec if demucs_segment_sec is None else f"{demucs_segment_sec}"
        print("Separator: demucs-vocals-inst")
        print(
            f"Demucs config: model={args.demucs_model}, device={args.demucs_device}, "
            f"segment_sec={demucs_seg_display}, overlap={args.demucs_overlap}, "
            f"loudness_match={not args.demucs_no_loudness_match}"
        )

    print(f"Bypass mode: {'disabled (always process)' if args.no_bypass else 'enabled (auto queue-pressure bypass)'}")

    vocal_gain, instrumental_gain = mix_controller.get_gains()
    print(
        f"Initial mix: vocal_mix={args.vocal_mix:.2f} "
        f"(vocals={vocal_gain:.2f}, instrumental={instrumental_gain:.2f})"
    )

    if args.mix_ui:
        if MixSliderUI(mix_controller).start():
            print("Mix UI: enabled")
        else:
            print("Mix UI: unavailable")

    harness = LiveLoopbackHarness(
        samplerate=args.samplerate,
        channels=args.channels,
        blocksize=args.blocksize,
        latency_buffers=args.latency_buffers,
        gain=args.gain,
        input_device=input_device,
        output_device=output_device,
        separator=separator,
        mix_controller=mix_controller,
        bypass_high_watermark=args.bypass_high_watermark,
        bypass_low_watermark=args.bypass_low_watermark,
        bypass_enabled=not args.no_bypass,
    )

    print("Starting live loopback harness. Press Ctrl+C to stop.")
    print(harness.format_runtime_stats())

    try:
        harness.run(duration=args.duration, verbose_interval=args.verbose_interval)
    except KeyboardInterrupt:
        print("Stopping...", flush=True)
    finally:
        print(harness.summary())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
