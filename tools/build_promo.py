"""Cut the C++mc promo from the four still frames with ffmpeg (Ken Burns + cross fades).

The ffmpeg binary comes from imageio-ffmpeg, so no system install is needed.
Music is generated separately and passed with --music.

Usage:  python tools/build_promo.py [--music <wav>] [--out <mp4>]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAMES = os.path.join(ROOT, "promo", "frames")
WORK = os.path.join(ROOT, "promo", "work")
OUT_DIR = os.path.join(ROOT, "promo")

CLIP_SECONDS = 4
XFADE = 0.7
FPS = 30
SIZE = (1920, 1080)


def run(args: list[str]) -> None:
    print("ffmpeg:", " ".join(args[:6]), "...")
    proc = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg failed ({proc.returncode}):\n{proc.stderr[-2000:]}")


def make_clip(frame: str, out: str, motion: str) -> None:
    """motion in {in, out, pan, in2}"""
    w, h = SIZE
    src = f"scale={w * 2}:{h * 2}:flags=lanczos"
    center = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    if motion == "in":
        zoom = "z='min(zoom+0.0028,1.34)'"
        xy = center
    elif motion == "out":
        zoom = "z='if(eq(on,0),1.34,max(zoom-0.0028,1.0))'"
        xy = center
    elif motion == "pan":
        zoom = "z='min(zoom+0.0016,1.18)'"
        xy = "x='(iw-iw/zoom)*(on/120)':y='ih/2-(ih/zoom/2)'"
    else:  # slower push for the end card
        zoom = "z='min(zoom+0.0020,1.22)'"
        xy = center
    vf = (f"{src},zoompan={zoom}:d={CLIP_SECONDS * FPS}:{xy}"
          f":s={w}x{h}:fps={FPS},format=yuv420p")
    run(["-loop", "1", "-i", frame, "-vf", vf, "-t", str(CLIP_SECONDS),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", out])


def concat(clips: list[str], out: str) -> float:
    total = CLIP_SECONDS * len(clips) - XFADE * (len(clips) - 1)
    args: list[str] = []
    for c in clips:
        args += ["-i", c]
    parts = []
    v_prev = "[0:v]"
    offset = CLIP_SECONDS - XFADE
    for i in range(1, len(clips)):
        v_out = f"[v{i}]" if i < len(clips) - 1 else "[vout]"
        parts.append(
            f"{v_prev}[{i}:v]xfade=transition=fade:duration={XFADE:.2f}:offset={offset:.2f}{v_out}"
        )
        v_prev = v_out
        offset += CLIP_SECONDS - XFADE
    run(args + ["-filter_complex", ";".join(parts), "-map", "[vout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", out])
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--music", default="")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "CppMc_promo.mp4"))
    a = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    plan = [
        ("frame1_logo.png", "clip1.mp4", "in"),
        ("frame2_code.png", "clip2.mp4", "pan"),
        ("frame3_stats.png", "clip3.mp4", "in"),
        ("frame4_end.png", "clip4.mp4", "out"),
    ]
    clips = []
    for frame, name, motion in plan:
        out = os.path.join(WORK, name)
        make_clip(os.path.join(FRAMES, frame), out, motion)
        clips.append(out)
    silent = os.path.join(WORK, "master.mp4")
    total = concat(clips, silent)
    print(f"silent master: {total:.2f}s")

    if a.music and os.path.exists(a.music):
        dur = f"{total:.2f}"
        run(["-i", silent, "-stream_loop", "-1", "-i", a.music, "-filter_complex",
             f"[1:a]atrim=0:{dur},asetpts=PTS-STARTPTS,afade=t=in:st=0:d=1.2,"
             f"afade=t=out:st={total - 2.2:.2f}:d=2.2,volume=0.9[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
             "-b:a", "160k", "-shortest", a.out])
    else:
        run(["-i", silent, "-c", "copy", a.out])

    print("wrote", a.out, os.path.getsize(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
