"""The Tk dialog for "Export video overlay": pick a gameplay video, set where the lap starts in it, check a preview frame,
export. All the work is in videosync.py (tested without a window or a real video); this file only draws it. Rendering and
frame extraction run on worker threads, so the window never freezes, and Cancel stops ffmpeg and removes the partial file."""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import theme, videosync
from .core import actions

PREVIEW_W = 640


def open_dialog(parent, df, ref_df, meta):
    """`df` is Lap A; `ref_df` (Lap B, or None) adds the delta to the HUD."""
    ff = videosync.configured_ffmpeg()
    if not ff:
        messagebox.showinfo("Export video overlay", videosync.INSTALL_HINT)
        return
    try:
        track = videosync.LapTrack(df, ref_df)
    except ValueError as e:
        messagebox.showinfo("Export video overlay", str(e))
        return
    win = tk.Toplevel(parent)
    win.title("TRACE - Export video overlay")
    win.configure(bg=theme.BG)
    theme.fit_window(win, 720, 700, 560, 520)
    win.transient(parent)
    st = {"info": None, "job": None, "busy": False, "photo": None}
    video, sync, fine = tk.StringVar(), tk.StringVar(value="0.00"), tk.DoubleVar(value=0.0)
    msg = tk.StringVar(value="Pick the video, then the time in it where the lap starts.")

    def lbl(text, **kw):
        return tk.Label(win, text=text, bg=theme.BG, fg=kw.pop("fg", theme.MID), font=(theme.MONO, 9), anchor="w", justify="left", **kw)

    def sync_value():
        try:
            return max(0.0, float(sync.get().replace(",", ".")))
        except ValueError:
            return 0.0

    def offset_value():
        return videosync.clamp_offset(fine.get())

    row = tk.Frame(win, bg=theme.BG)
    row.pack(fill="x", padx=14, pady=(12, 4))
    tk.Entry(row, textvariable=video, bg=theme.BG3, fg=theme.TXT, insertbackground=theme.TXT, relief="flat",
             font=(theme.MONO, 9)).pack(side="left", fill="x", expand=True, ipady=3)

    def browse():
        p = filedialog.askopenfilename(title="Gameplay video", filetypes=[("Video", "*.mp4 *.mkv *.mov *.webm *.avi"), ("All", "*.*")])
        if p:
            video.set(p)
            st["info"] = None
            update_preview()
    tk.Button(row, text="Browse...", command=browse, bg=theme.BG3, fg=theme.TXT, relief="flat", font=(theme.MONO, 9), padx=10).pack(side="left", padx=6)

    row2 = tk.Frame(win, bg=theme.BG)
    row2.pack(fill="x", padx=14, pady=4)
    lbl("Lap starts at (seconds into the video):").pack(in_=row2, side="left")
    tk.Spinbox(row2, textvariable=sync, from_=0, to=36000, increment=0.5, width=9, format="%.2f", bg=theme.BG3, fg=theme.TXT,
               buttonbackground=theme.BG3, relief="flat", font=(theme.MONO, 10), command=lambda: update_preview()).pack(side="left", padx=8)
    lbl("Fine offset (-5 to +5 s):").pack(anchor="w", padx=14, pady=(6, 0))
    tk.Scale(win, variable=fine, from_=-videosync.OFFSET_RANGE_S, to=videosync.OFFSET_RANGE_S, resolution=videosync.OFFSET_STEP_S,
             orient="horizontal", length=420, bg=theme.BG, fg=theme.TXT, troughcolor=theme.BG3, highlightthickness=0,
             command=lambda _v: None).pack(anchor="w", padx=14)
    tk.Button(win, text="Update preview", command=lambda: update_preview(), bg=theme.BG3, fg=theme.TXT, relief="flat",
              font=(theme.MONO, 9), padx=10).pack(anchor="w", padx=14, pady=4)
    pic = tk.Label(win, bg=theme.CBG, text="(preview)", fg=theme.DIM, font=(theme.MONO, 9))
    pic.pack(padx=14, pady=4)
    status = tk.Label(win, textvariable=msg, bg=theme.BG, fg=theme.MID, font=(theme.MONO, 9), anchor="w", justify="left", wraplength=640)
    status.pack(fill="x", padx=14)
    bar = ttk.Progressbar(win, maximum=1.0, length=420)
    bar.pack(anchor="w", padx=14, pady=6)
    btns = tk.Frame(win, bg=theme.BG)
    btns.pack(fill="x", padx=14, pady=8)

    def say(text):
        if win.winfo_exists():
            msg.set(text)

    def update_preview():
        p = video.get().strip()
        if not p or st["busy"]:
            return
        say("Reading a frame...")

        def work():
            try:
                if st["info"] is None:
                    st["info"] = videosync.probe(ff, p)
                img = videosync.preview_image(ff, p, st["info"], track, sync_value(), offset_value())
                img.thumbnail((PREVIEW_W, PREVIEW_W))
                from PIL import ImageTk

                def show():
                    if pic.winfo_exists():
                        st["photo"] = ImageTk.PhotoImage(img.convert("RGB"))
                        pic.config(image=st["photo"], text="")
                        say("Preview shows the frame at the sync point with the HUD as it will be exported.")
                win.after(0, show)
            except Exception as e:
                err = str(e)
                win.after(0, lambda: say(f"Couldn't make a preview: {err}"))
        threading.Thread(target=work, daemon=True, name="trace-video-preview").start()

    def export():
        p = video.get().strip()
        if not p or not Path(p).is_file():
            messagebox.showinfo("Export video overlay", "Pick a video file first.")
            return
        out = filedialog.asksaveasfilename(title="Save video as", defaultextension=".mp4", initialfile=videosync.default_output_name(meta, p),
                                           filetypes=[("MP4 video", "*.mp4"), ("All", "*.*")])
        if not out:
            return
        if Path(out).resolve() == Path(p).resolve():
            messagebox.showerror("Export video overlay", "Choose a different file name: the export can't overwrite the original video.")
            return
        st["busy"] = True
        go.config(state="disabled")
        cancel.config(text="Cancel export")

        def work():
            try:
                info = st["info"] or videosync.probe(ff, p)
                job = videosync.ExportJob(ff, p, out, track, info, sync_value(), offset_value())
                st["job"] = job
                rep = job.run(lambda f: win.after(0, lambda: (bar.config(value=f), say(f"Exporting... {f * 100:.0f}%"))))
                win.after(0, lambda: done(rep, None))
            except Exception as e:
                err = str(e)
                win.after(0, lambda: done(None, err))

        def done(rep, err):
            st["busy"] = False
            if not win.winfo_exists():
                return
            go.config(state="normal")
            cancel.config(text="Close")
            if err:
                say(f"Export failed: {err}")
                messagebox.showerror("Export video overlay", err)
            elif rep.cancelled:
                say("Export cancelled; the partial file was removed.")
            else:
                say(f"Saved {out}")
                messagebox.showinfo("Export video overlay", f"Saved {Path(out).name}\n{rep.frames} HUD frames; audio copied untouched.")
        threading.Thread(target=work, daemon=True, name="trace-video-export").start()

    def close():
        if st["busy"] and st["job"] is not None:
            st["job"].cancel()
            say("Cancelling...")
            return
        win.destroy()
    go = tk.Button(btns, text="Export video", command=export, bg=theme.RED, fg=theme.BG, relief="flat", font=(theme.MONO, 9, "bold"),
                   padx=14, pady=4)
    go.pack(side="left")
    cancel = tk.Button(btns, text="Close", command=close, bg=theme.BG3, fg=theme.TXT, relief="flat", font=(theme.MONO, 9), padx=14, pady=4)
    cancel.pack(side="left", padx=8)
    win.protocol("WM_DELETE_WINDOW", close)
    _ = actions   # shared with the terminal UI: the same videosync calls
