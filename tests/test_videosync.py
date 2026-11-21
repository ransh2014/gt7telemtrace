"""Video sync: sync maths, interpolation, HUD drawing, ffmpeg-missing path, exact command line, cancel. No real video or
ffmpeg is used: processes are faked and frames come from Pillow."""
import io

import numpy as np
import pandas as pd
import pytest
from _fixtures import full_lap_data

from gt7telem import videosync as vs


def lap_df():
    return pd.DataFrame(full_lap_data()["samples"])


def test_sync_maths():
    assert vs.clamp_offset(9) == 5.0 and vs.clamp_offset(-9) == -5.0 and vs.clamp_offset(0.123) == 0.12
    assert vs.lap_time_at(12.5, 10.0, 0.5) == pytest.approx(2.0)
    assert vs.video_time_of(2.0, 10.0, 0.5) == pytest.approx(12.5)
    v0, n, lap0 = vs.frame_window(60.0, 10.0, 0.0, 100.0, 30.0)
    assert (v0, n, lap0) == (10.0, 1801, 0.0)
    v0, n, lap0 = vs.frame_window(60.0, 10.0, 0.0, 40.0, 30.0)          # the video ends before the lap does
    assert v0 == 10.0 and n == 901
    v0, n, lap0 = vs.frame_window(60.0, 2.0, -5.0, 100.0, 30.0)         # the lap began before the video: skip into it
    assert v0 == 0.0 and lap0 == pytest.approx(3.0)
    assert vs.frame_window(60.0, 500.0, 0.0, 100.0, 30.0)[1] == 0       # lap outside the video


def test_fps_and_probe_parsing():
    assert vs.fps_arg(30) == "30" and vs.fps_arg(59.94) == "60000/1001" and vs.fps_arg(29.97) == "30000/1001"
    text = ("Input #0, mov,mp4, from 'a.mp4':\n  Duration: 00:01:30.50, start: 0.0, bitrate: 9000 kb/s\n"
            "  Stream #0:0: Video: h264 (High), yuv420p, 1920x1080 [SAR 1:1 DAR 16:9], 9000 kb/s, 59.94 fps, 60 tbr\n"
            "  Stream #0:1: Audio: aac, 48000 Hz, stereo\n")
    info = vs.parse_probe(text)
    assert (info.width, info.height, info.fps, info.duration) == (1920, 1080, 59.94, 90.5)
    with pytest.raises(ValueError):
        vs.parse_probe("Stream #0:0: Audio: aac")


def test_ffmpeg_is_optional():
    assert vs.find_ffmpeg("", which=lambda n: None) is None
    assert vs.find_ffmpeg("", which=lambda n: "/usr/bin/ffmpeg") == "/usr/bin/ffmpeg"
    assert vs.find_ffmpeg("C:/nope/ffmpeg.exe", which=lambda n: None) is None
    assert "ffmpeg" in vs.INSTALL_HINT and "FFMPEG_PATH" in vs.INSTALL_HINT


def test_lap_is_interpolated_to_any_time_and_gear_is_nearest():
    df = lap_df()
    tr = vs.LapTrack(df)
    assert tr.at(-0.1) is None and tr.at(tr.duration + 1) is None
    s = tr.at(tr.duration / 2)
    t = df["t"].to_numpy(float) - df["t"].iloc[0]
    assert s.speed == pytest.approx(float(np.interp(tr.duration / 2, t, df["speed_kmh"])))
    assert s.gear in set(df["gear"].astype(int)) and 0 <= s.throttle <= 1 and 0 <= s.rpm_frac <= 1
    assert tr.at(0).delta is None


def test_delta_against_a_reference_is_positive_when_slower():
    fast = lap_df()
    slow = fast.copy()
    slow["t"] = fast["t"] * 1.05
    tr = vs.LapTrack(slow, fast)
    assert tr.has_reference and tr.at(tr.duration * 0.8).delta > 0
    assert vs.LapTrack(fast, slow).at(fast["t"].iloc[-1] * 0.8).delta < 0


def test_hud_frame_pixels_and_blank_frames():
    tr = vs.LapTrack(lap_df())
    r = vs.HudRenderer(tr, vs.hud_size(1080))
    img = r.render(tr.at(tr.duration / 2))
    assert img.size == (720, 300) and img.mode == "RGBA"
    arr = np.asarray(img)
    assert arr[150, 360, 3] > 0 and arr[2, 2, 3] == 0              # the panel is drawn, its rounded corner is transparent
    assert (arr[..., 3] > 0).mean() > 0.4 and (arr[..., :3] > 200).any()    # text and bars were drawn
    assert r.raw(None) == bytes(720 * 300 * 4) and len(r.raw(tr.at(1.0))) == 720 * 300 * 4
    w, h = vs.hud_size(540)
    assert w % 2 == 0 and h % 2 == 0 and w < 720


def test_ffmpeg_argv_is_exact():
    argv = vs.build_ffmpeg_argv("ffmpeg", "in.mp4", "out.mp4", 720, 300, 24, 756, 59.94, 12.5)
    assert argv == ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-y", "-i", "in.mp4",
                    "-itsoffset", "12.500", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "720x300", "-r", "60000/1001", "-i", "-",
                    "-filter_complex", "[0:v][1:v]overlay=24:756:eof_action=pass[v]", "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy",
                    "-movflags", "+faststart", "out.mp4"]
    assert "copy" in argv[argv.index("-c:a") + 1]                   # audio is copied, never re-encoded


class FakeProc:
    def __init__(self, argv, **kw):
        self.argv, self.writes, self.code, self.terminated = argv, [], 0, False
        self.stdin = self
        self.stderr = iter([b"warning: test\n"])

    def write(self, b):
        self.writes.append(len(b))

    def close(self):
        pass

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.terminated = True

    def kill(self):
        pass


INFO = vs.VideoInfo(1920, 1080, 30.0, 120.0)


def test_export_pipes_one_hud_frame_per_video_frame(tmp_path):
    tr = vs.LapTrack(lap_df())
    made = []
    job = vs.ExportJob("ffmpeg", "in.mp4", tmp_path / "o.mp4", tr, INFO, 10.0, popen=lambda a, **k: made.append(FakeProc(a, **k)) or made[0])
    seen = []
    rep = job.run(seen.append)
    _argv, v0, n, _lap0 = job.plan()
    assert rep.frames == n == len(made[0].writes) and seen[-1] == 1.0 and not rep.cancelled and v0 == 10.0
    assert set(made[0].writes) == {720 * 300 * 4}


def test_cancel_stops_ffmpeg_and_removes_the_partial_file(tmp_path):
    tr = vs.LapTrack(lap_df())
    out = tmp_path / "o.mp4"
    out.write_bytes(b"partial")
    made = []
    job = vs.ExportJob("ffmpeg", "in.mp4", out, tr, INFO, 0.0, popen=lambda a, **k: made.append(FakeProc(a, **k)) or made[0])
    job.cancel()
    rep = job.run()
    assert rep.cancelled and rep.path is None and made[0].terminated and not out.exists()


def test_a_lap_outside_the_video_and_an_ffmpeg_error_are_clear_errors(tmp_path):
    tr = vs.LapTrack(lap_df())
    with pytest.raises(vs.ExportError, match="not inside"):
        vs.ExportJob("ffmpeg", "in.mp4", tmp_path / "o.mp4", tr, INFO, 5000.0, popen=FakeProc).run()

    def failing(argv, **kw):
        p = FakeProc(argv, **kw)
        p.code = 1
        return p
    with pytest.raises(vs.ExportError, match="ffmpeg stopped"):
        vs.ExportJob("ffmpeg", "in.mp4", tmp_path / "o.mp4", tr, INFO, 1.0, popen=failing).run()


def test_preview_draws_the_hud_on_a_decoded_frame():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (30, 90, 30)).save(buf, "PNG")

    class R:
        returncode, stdout = 0, buf.getvalue()
    tr = vs.LapTrack(lap_df())
    img = vs.preview_image("ffmpeg", "v.mp4", vs.VideoInfo(1280, 720, 30, 60), tr, 5.0, 0.0, 5.0 + tr.duration / 2, run=lambda *a, **k: R)
    assert img.size == (1280, 720) and img.getpixel((600, 300)) == (30, 90, 30, 255)    # untouched away from the HUD
    x, y = vs.hud_position(1280, 720, *vs.hud_size(720))
    assert img.getpixel((x + 5, y + 100)) != (30, 90, 30, 255)                          # the HUD strip is composited in


def test_registry_cli_and_front_ends_offer_it():
    from pathlib import Path

    from gt7telem import cli
    from gt7telem.core import registry
    assert ("video", registry.VIDEO_EXPORT_LABEL) in registry.LAP_ACTIONS
    src = Path(vs.__file__).parent
    assert "registry.VIDEO_EXPORT_LABEL" in (src / "lap_analyst.py").read_text(encoding="utf-8")
    assert 'kind == "video"' in (src / "tui" / "app.py").read_text(encoding="utf-8")
    assert "videosync.INSTALL_HINT" in (src / "lap_analyst.py").read_text(encoding="utf-8")      # greyed out with the hint
    assert "video" in cli.build_parser().format_help()
