"""Video sync: burn a lap's telemetry HUD into a gameplay video.

Pure core (no tkinter): the sync maths, the lap interpolated to the video's frame rate, the HUD drawn with Pillow (a
dependency of matplotlib, so always present), the ffmpeg command line, and a cancellable export job. ffmpeg itself is an
optional external program: found on PATH or through the FFMPEG_PATH setting, and when it is missing the tool says so and
nothing else breaks. Audio is copied untouched; video is re-encoded on the CPU.

How it lines up: the user gives the sync point, the time in the video where the lap starts (seconds, plus a fine offset of
up to 5 s in 0.01 s steps). Video time ``v`` shows lap time ``v - (sync + offset)``. Only the stretch of video that shows
the lap gets HUD frames (a small RGBA strip piped to ffmpeg's overlay filter), so a long video costs no more than the lap.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from . import theme

FFMPEG_SETTING = "FFMPEG_PATH"
OFFSET_RANGE_S = 5.0
OFFSET_STEP_S = 0.01
HUD_BASE_W, HUD_BASE_H = 720, 300      # the HUD at 1080p; scaled with the video height
MIN_LAP_S = 1.0
INSTALL_HINT = "ffmpeg not found. Install it from ffmpeg.org (or your package manager) and restart TRACE, or set FFMPEG_PATH in settings.json."


# ---- ffmpeg discovery ---------------------------------------------------------------------------------------------
def find_ffmpeg(setting: str = "", which: Callable[[str], str | None] = shutil.which) -> str | None:
    """The ffmpeg program to use: the FFMPEG_PATH setting if it points at one (a file, or a folder holding it), else PATH."""
    s = (setting or "").strip().strip('"')
    if s:
        p = Path(s)
        if p.is_file():
            return str(p)
        for name in ("ffmpeg.exe", "ffmpeg"):
            if (p / name).is_file():
                return str(p / name)
    return which("ffmpeg")


def configured_ffmpeg() -> str | None:
    from . import config
    return find_ffmpeg(getattr(config, "FFMPEG_PATH", ""))


# ---- probing the video ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    duration: float | None


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM = re.compile(r"Stream #\d+:\d+[^\n]*?Video:[^\n]*?,\s*(\d{2,5})x(\d{2,5})[^\n]*")
_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")
_TBR = re.compile(r"(\d+(?:\.\d+)?)\s*tbr")


def parse_probe(text: str) -> VideoInfo:
    """Read the size, frame rate and length out of `ffmpeg -i FILE` output. Raises ValueError for a file with no video."""
    m = _STREAM.search(text)
    if not m:
        raise ValueError("ffmpeg could not find a video stream in that file")
    line = m.group(0)
    fps_m = _FPS.search(line) or _TBR.search(line)
    d = _DURATION.search(text)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else None
    return VideoInfo(int(m.group(1)), int(m.group(2)), float(fps_m.group(1)) if fps_m else 30.0, dur)


def probe(ffmpeg: str, video, run: Callable = subprocess.run) -> VideoInfo:
    r = run([ffmpeg, "-hide_banner", "-i", str(video)], capture_output=True, text=True, timeout=30)
    return parse_probe((r.stderr or "") + (r.stdout or ""))     # ffmpeg prints the file info to stderr and exits non-zero


def fps_arg(fps: float) -> str:
    """ffmpeg's -r value: 59.94 -> '60000/1001', 30 -> '30'."""
    for base in (24000, 30000, 48000, 60000, 120000):          # the NTSC rates 23.976, 29.97, 47.95, 59.94, 119.88
        if abs(fps - base / 1001) < 0.011:
            return f"{base}/1001"
    fr = Fraction(fps).limit_denominator(1001)
    return str(fr.numerator) if fr.denominator == 1 else f"{fr.numerator}/{fr.denominator}"


# ---- sync maths ---------------------------------------------------------------------------------------------------
def clamp_offset(offset_s: float) -> float:
    """The fine offset: within +-5 s, on the 0.01 s grid."""
    return round(max(-OFFSET_RANGE_S, min(OFFSET_RANGE_S, float(offset_s))) / OFFSET_STEP_S) * OFFSET_STEP_S


def effective_sync(sync_s: float, offset_s: float = 0.0) -> float:
    return float(sync_s) + clamp_offset(offset_s)


def lap_time_at(video_t: float, sync_s: float, offset_s: float = 0.0) -> float:
    """Lap time shown at video time `video_t` (negative before the lap starts)."""
    return video_t - effective_sync(sync_s, offset_s)


def video_time_of(lap_t: float, sync_s: float, offset_s: float = 0.0) -> float:
    return lap_t + effective_sync(sync_s, offset_s)


def frame_window(lap_duration: float, sync_s: float, offset_s: float, video_duration: float | None, fps: float) -> tuple[float, int, float]:
    """(video time of the first HUD frame, how many frames, lap time at that first frame): the part of the video that
    shows the lap. Empty (0 frames) when the lap lies outside the video."""
    s = effective_sync(sync_s, offset_s)
    v0 = max(0.0, s)
    v1 = s + lap_duration
    if video_duration is not None:
        v1 = min(v1, video_duration)
    if v1 <= v0:
        return v0, 0, v0 - s
    return v0, int(math.floor((v1 - v0) * fps)) + 1, v0 - s


# ---- the lap, interpolated ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class State:
    lap_t: float
    speed: float
    gear: int
    rpm: float
    rpm_frac: float
    throttle: float
    brake: float
    steering: float
    delta: float | None     # + = slower than the reference lap at this point
    x: float
    z: float
    pos: float


def _col(df: pd.DataFrame, name: str, default=0.0) -> np.ndarray:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default).to_numpy(dtype=float)
    return np.full(len(df), default)


class LapTrack:
    """One lap as arrays on its own clock (0 = the start of the lap), ready to be read at any time."""

    def __init__(self, df: pd.DataFrame, ref: pd.DataFrame | None = None):
        if df is None or len(df) < 20 or "t" not in df.columns:
            raise ValueError("This lap has too few samples to put on a video.")
        t = np.maximum.accumulate(_col(df, "t"))
        self.t = t - t[0]
        if float(self.t[-1]) < MIN_LAP_S:
            raise ValueError("This lap has no usable timestamps.")
        self.duration = float(self.t[-1])
        self.speed = _col(df, "speed_kmh")
        self.rpm = _col(df, "rpm")
        self.gear = _col(df, "gear")
        self.throttle = _col(df, "throttle")
        self.brake = _col(df, "brake")
        self.steering = _col(df, "steering")
        self.x, self.z = _col(df, "world_x"), _col(df, "world_z")
        self.pos = _col(df, "track_position")
        mx = float(np.max(_col(df, "max_rpm"))) if "max_rpm" in df.columns else 0.0
        self.max_rpm = mx if mx > 1000 else max(float(self.rpm.max()) * 1.05, 1000.0)
        self.has_map = bool((np.abs(self.x) + np.abs(self.z)).max() > 0 and np.ptp(self.x) + np.ptp(self.z) > 1.0)
        self._ref = None
        if ref is not None and len(ref) >= 20 and "t" in ref.columns and "track_position" in ref.columns:
            rt = np.maximum.accumulate(_col(ref, "t"))
            rp = _col(ref, "track_position")
            order = np.argsort(rp, kind="stable")
            if float(rp.max() - rp.min()) > 100:
                self._ref = (rp[order], (rt - rt[0])[order])

    @property
    def has_reference(self) -> bool:
        return self._ref is not None

    def at(self, lap_t: float) -> State | None:
        """The interpolated state at lap time `lap_t`, or None outside the lap. Gear is the nearest sample, not a blend."""
        if lap_t < 0 or lap_t > self.duration:
            return None
        i = int(np.clip(np.searchsorted(self.t, lap_t, side="left"), 0, len(self.t) - 1))
        if i > 0 and abs(self.t[i - 1] - lap_t) < abs(self.t[i] - lap_t):
            i -= 1

        def f(a):
            return float(np.interp(lap_t, self.t, a))
        pos = f(self.pos)
        delta = None
        if self._ref is not None:
            rp, rt = self._ref
            delta = lap_t - float(np.interp(pos, rp, rt))
        return State(lap_t, f(self.speed), int(round(self.gear[i])), f(self.rpm), min(1.0, max(0.0, f(self.rpm) / self.max_rpm)),
                     min(1.0, max(0.0, f(self.throttle))), min(1.0, max(0.0, f(self.brake))), min(1.0, max(-1.0, f(self.steering))),
                     delta, f(self.x), f(self.z), pos)


# ---- the HUD ----------------------------------------------------------------------------------------------------
def _rgb(hex_: str, a: int = 255):
    h = hex_.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def fmt_lap_time(t: float) -> str:
    t = max(0.0, t)
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:06.3f}"


def hud_size(video_h: int) -> tuple[int, int]:
    """Even width and height for the HUD strip at this video height."""
    s = max(0.35, video_h / 1080.0)
    return int(HUD_BASE_W * s) // 2 * 2, int(HUD_BASE_H * s) // 2 * 2


def hud_position(video_w: int, video_h: int, hud_w: int, hud_h: int) -> tuple[int, int]:
    margin = int(24 * max(0.35, video_h / 1080.0))
    return margin, max(0, video_h - hud_h - margin)


class HudRenderer:
    """Draws the HUD for one State onto a transparent RGBA image. The panel and the track outline are drawn once."""

    def __init__(self, track: LapTrack, size: tuple[int, int]):
        from PIL import Image, ImageDraw, ImageFont
        self._Image, self._Draw = Image, ImageDraw
        self.track, self.w, self.h = track, size[0], size[1]
        self.s = self.w / HUD_BASE_W

        def font(file, px):
            try:
                return ImageFont.truetype(str(theme.FONT_DIR / file), max(6, int(px * self.s)))
            except OSError:
                return ImageFont.load_default()
        self.f_big, self.f_gear = font("JetBrainsMono-Bold.ttf", 104), font("JetBrainsMono-Bold.ttf", 76)
        self.f_mid, self.f_small = font("JetBrainsMono-Bold.ttf", 34), font("JetBrainsMono-Regular.ttf", 17)
        self.base = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(self.base)
        d.rounded_rectangle([0, 0, self.w - 1, self.h - 1], radius=int(14 * self.s), fill=_rgb(theme.BG, 200), outline=_rgb(theme.LINE2, 255),
                            width=max(1, int(self.s)))
        self.map_box = (int(468 * self.s), int(78 * self.s), int(696 * self.s), int(280 * self.s))
        self._map = self._fit_map()
        if self._map is not None and len(self._map) > 1:
            d.line([tuple(p) for p in self._map], fill=_rgb(theme.MID, 255), width=max(2, int(3 * self.s)), joint="curve")
        self.blank = bytes(self.w * self.h * 4)

    def _fit_map(self):
        tr = self.track
        if not tr.has_map:
            return None
        step = max(1, len(tr.x) // 600)
        xs, zs = tr.x[::step], tr.z[::step]
        x0, x1, z0, z1 = float(xs.min()), float(xs.max()), float(zs.min()), float(zs.max())
        bx0, by0, bx1, by1 = self.map_box
        k = min((bx1 - bx0) / max(x1 - x0, 1e-6), (by1 - by0) / max(z1 - z0, 1e-6))
        self._k, self._o = k, (bx0 + ((bx1 - bx0) - (x1 - x0) * k) / 2 - x0 * k, by0 + ((by1 - by0) - (z1 - z0) * k) / 2 + z1 * k)
        return np.column_stack([xs * k + self._o[0], -zs * k + self._o[1]])

    def _pt(self, x: float, z: float):
        return x * self._k + self._o[0], -z * self._k + self._o[1]

    def render(self, st: State):
        img = self.base.copy()
        d, s = self._Draw.Draw(img), self.s
        txt, mid, dim = _rgb(theme.TXT), _rgb(theme.MID), _rgb(theme.DIM)
        d.text((24 * s, 6 * s), f"{st.speed:.0f}", font=self.f_big, fill=txt)
        d.text((26 * s, 126 * s), "km/h", font=self.f_small, fill=mid)
        d.text((250 * s, 22 * s), "N" if st.gear == 0 else "R" if st.gear < 0 else str(st.gear), font=self.f_gear, fill=_rgb(theme.GOLD))
        d.text((254 * s, 106 * s), "gear", font=self.f_small, fill=mid)
        # rpm bar
        x0, x1, y0, y1 = 24 * s, 340 * s, 160 * s, 182 * s
        d.rectangle([x0, y0, x1, y1], fill=_rgb(theme.BG3))
        hot = st.rpm_frac > 0.92
        d.rectangle([x0, y0, x0 + (x1 - x0) * st.rpm_frac, y1], fill=_rgb(theme.RED_2 if hot else theme.TEAL_2))
        d.text((x0, y1 + 3 * s), f"{st.rpm:.0f} rpm", font=self.f_small, fill=mid)
        # throttle and brake bars
        for i, (val, colour, label) in enumerate(((st.throttle, theme.GREEN, "THR"), (st.brake, theme.RED_2, "BRK"))):
            bx = (362 + i * 44) * s
            top, bot = 24 * s, 150 * s
            d.rectangle([bx, top, bx + 30 * s, bot], fill=_rgb(theme.BG3))
            d.rectangle([bx, bot - (bot - top) * val, bx + 30 * s, bot], fill=_rgb(colour))
            d.text((bx - 1 * s, bot + 4 * s), label, font=self.f_small, fill=mid)
        # steering strip
        sy0, sy1, sx0, sx1 = 246 * s, 262 * s, 24 * s, 340 * s
        d.rectangle([sx0, sy0, sx1, sy1], fill=_rgb(theme.BG3))
        cx = (sx0 + sx1) / 2
        mk = cx + st.steering * (sx1 - sx0) / 2
        d.rectangle([min(cx, mk), sy0, max(cx, mk), sy1], fill=_rgb(theme.GOLD))
        d.rectangle([cx - 1, sy0 - 3 * s, cx + 1, sy1 + 3 * s], fill=txt)
        d.text((sx0, sy1 + 4 * s), "steering", font=self.f_small, fill=dim)
        # lap timer and delta
        d.text((468 * s, 14 * s), fmt_lap_time(st.lap_t), font=self.f_mid, fill=txt)
        if st.delta is not None:
            ahead = st.delta <= 0
            d.text((468 * s, 50 * s), f"{st.delta:+.2f} s", font=self.f_small, fill=_rgb(theme.GREEN if ahead else theme.RED_2))
            d.text((540 * s, 50 * s), "vs reference", font=self.f_small, fill=dim)
        # track map with the car
        if self._map is not None:
            cx2, cz2 = self._pt(st.x, st.z)
            r = 7 * s
            d.ellipse([cx2 - r, cz2 - r, cx2 + r, cz2 + r], fill=txt, outline=_rgb(theme.RED), width=max(2, int(2 * s)))
        return img

    def raw(self, st: State | None) -> bytes:
        """RGBA bytes for ffmpeg (a blank, fully transparent frame when the lap is not on screen)."""
        return self.blank if st is None else self.render(st).tobytes()


# ---- one frame, for the preview ---------------------------------------------------------------------------------
def extract_frame(ffmpeg: str, video, at_s: float, run: Callable = subprocess.run):
    """One video frame as a Pillow image (ffmpeg decodes it to PNG on stdout)."""
    import io

    from PIL import Image
    r = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, at_s):.3f}", "-i", str(video), "-frames:v", "1",
             "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60)
    if r.returncode != 0 or not r.stdout:
        raise ValueError("ffmpeg could not read a frame at that time")
    return Image.open(io.BytesIO(r.stdout)).convert("RGBA")


def preview_image(ffmpeg: str, video, info: VideoInfo, track: LapTrack, sync_s: float, offset_s: float = 0.0,
                  at_video_s: float | None = None, run: Callable = subprocess.run):
    """The video frame at the sync point (or `at_video_s`) with the HUD burned in, exactly as the export would draw it."""
    from PIL import Image
    s = effective_sync(sync_s, offset_s)
    t = s if at_video_s is None else at_video_s
    frame = extract_frame(ffmpeg, video, t, run)
    hw, hh = hud_size(frame.height)
    x, y = hud_position(frame.width, frame.height, hw, hh)
    st = track.at(t - s)
    if st is not None:
        hud = HudRenderer(track, (hw, hh)).render(st)
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        layer.paste(hud, (x, y))
        frame = Image.alpha_composite(frame, layer)
    return frame


# ---- the export ----------------------------------------------------------------------------------------------------
def build_ffmpeg_argv(ffmpeg: str, video, out, hud_w: int, hud_h: int, x: int, y: int, fps: float, start_s: float) -> list[str]:
    """The exact ffmpeg command: the video in, the HUD strip on stdin as raw RGBA starting `start_s` into the video, the
    overlay filter placing it, audio copied untouched, H.264 out."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y",
        "-i", str(video),
        "-itsoffset", f"{start_s:.3f}", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{hud_w}x{hud_h}", "-r", fps_arg(fps), "-i", "-",
        "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:eof_action=pass[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ]


class ExportError(Exception):
    pass


@dataclass
class ExportReport:
    path: Path | None = None
    frames: int = 0
    cancelled: bool = False
    notes: list[str] = field(default_factory=list)


class ExportJob:
    """Renders the HUD frames and pipes them to ffmpeg. Blocking `run()`, so call it from a worker thread; `cancel()` may be
    called from any thread and stops ffmpeg and deletes the partial file."""

    def __init__(self, ffmpeg: str, video, out, track: LapTrack, info: VideoInfo, sync_s: float, offset_s: float = 0.0,
                 popen: Callable = subprocess.Popen):
        self.ffmpeg, self.video, self.out, self.track, self.info = ffmpeg, video, Path(out), track, info
        self.sync_s, self.offset_s, self._popen = sync_s, clamp_offset(offset_s), popen
        self._cancel = threading.Event()
        self.progress = 0.0

    def cancel(self) -> None:
        self._cancel.set()

    def plan(self) -> tuple[list[str], float, int, float]:
        """(ffmpeg argv, first video time, frame count, lap time at the first frame)."""
        hw, hh = hud_size(self.info.height)
        x, y = hud_position(self.info.width, self.info.height, hw, hh)
        v0, n, lap0 = frame_window(self.track.duration, self.sync_s, self.offset_s, self.info.duration, self.info.fps)
        return build_ffmpeg_argv(self.ffmpeg, self.video, self.out, hw, hh, x, y, self.info.fps, v0), v0, n, lap0

    def run(self, progress: Callable[[float], None] | None = None) -> ExportReport:
        argv, v0, n, lap0 = self.plan()
        if n <= 0:
            raise ExportError("The lap is not inside the video at that sync point. Check the sync time.")
        hw, hh = hud_size(self.info.height)
        hud = HudRenderer(self.track, (hw, hh))
        rep = ExportReport(path=self.out)
        proc = self._popen(argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        tail: deque[str] = deque(maxlen=12)

        def drain():
            try:
                for line in proc.stderr:
                    tail.append(line.decode("utf-8", "replace").rstrip() if isinstance(line, bytes) else str(line).rstrip())
            except Exception:
                pass
        threading.Thread(target=drain, daemon=True).start()
        try:
            for k in range(n):
                if self._cancel.is_set():
                    rep.cancelled = True
                    break
                st = self.track.at(lap0 + k / self.info.fps)
                proc.stdin.write(hud.raw(st))
                rep.frames = k + 1
                self.progress = (k + 1) / n
                if progress:
                    progress(self.progress)
            if not rep.cancelled:
                proc.stdin.close()
                code = proc.wait()
                if code != 0:
                    raise ExportError("ffmpeg stopped with an error: " + " | ".join(tail))
            else:
                self._stop(proc)
        except (BrokenPipeError, OSError) as e:
            self._stop(proc)
            self.out.unlink(missing_ok=True)
            raise ExportError("ffmpeg stopped early: " + (" | ".join(tail) or str(e))) from e
        except BaseException:
            self._stop(proc)
            self.out.unlink(missing_ok=True)
            raise
        if rep.cancelled:
            self.out.unlink(missing_ok=True)
            rep.path = None
        return rep

    @staticmethod
    def _stop(proc) -> None:
        for fn in (lambda: proc.stdin.close(), proc.terminate):
            try:
                fn()
            except Exception:
                pass
        try:
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def default_output_name(meta: dict | None, video) -> str:
    """A file name for the export, next to the conventions of the other exports: the lap's label, safe characters only."""
    base = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (Path(str(video)).stem or "video")).strip("._") or "video"
    return f"{base}_trace_hud.mp4"
