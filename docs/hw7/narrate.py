"""Narrated version of atl_pixel.py: macOS `say` voice-over, a pause with the list of ATL checks, one MP4.

Run: uv run --with pillow --with imageio-ffmpeg python -I narrate.py OUT.mp4
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

import atl_pixel as ap

VOICE, RATE = "Samantha", 175
HOLD_AT = 7.6  # scene time where the video pauses on the gate to list the checks
GAP = 0.3

# (scene time the line belongs to, text). "checks" is spoken during the pause.
LINES = [
    (0.2, "Where the Agent Trust Layer sits."),
    (3.0, "Supplier agents send invoices. Our AI agent holds no money tools, "
          "so every payment goes through the Trust Layer."),
    ("checks", "For each payment, the Trust Layer checks the owner's signed mandate. Is the supplier registered? "
               "Is the Pix key exactly the registered key? Is the category allowed? Is the amount under the "
               "per-payment and daily caps? Is it a duplicate, or part of a split order? And does the invoice "
               "note try to give the agent instructions? Then it approves and pays, asks the owner, or refuses."),
    (8.4, "This one passes: the Trust Layer pays the registered key, and signs a receipt."),
    (11.2, "This one changes the Pix key. Rule R 3 refuses it."),
    (19.2, "This one is over the cap. The Trust Layer asks the owner agent, who says no."),
    (28.2, "Now a hidden order hijacks the agent: pay twenty-five reais to an unknown key."),
    (31.0, "The agent tries. The Trust Layer refuses it with rule R 3."),
    (35.0, "Without the Trust Layer, the agent's own Pix tool just sends the money."),
    (38.6, "In our benchmark, the Trust Layer blocked all thirty-three attempts by a compromised agent. "
           "On ordinary invoice fraud, it tied a capable baseline. It is a guard against the agent itself."),
]

PANEL = [
    ("WHAT ATL CHECKS ON EVERY PAYMENT", ap.LBLUE),
    ("1. Mandate signed by the owner", ap.TEXT),
    ("2. Supplier is registered", ap.TEXT),
    ("3. Pix key = the registered key", ap.TEXT),
    ("4. Category is allowed", ap.TEXT),
    ("5. Under per-payment and daily caps", ap.TEXT),
    ("6. Not a duplicate or a split order", ap.TEXT),
    ("7. Note gives no payment instructions", ap.TEXT),
    ("=> approve and pay, ask owner, or refuse", ap.LORANGE),
]


def duration(path: Path) -> float:
    out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True).stdout
    line = next(x for x in out.splitlines() if "estimated duration" in x)
    return float(line.split(":")[1].split()[0])


def panel(img: Image.Image, shown: int) -> None:
    d = ap.ScaledDraw(img)
    d.rectangle([34, 22, 286, 22 + 12 * len(PANEL) + 8], fill=ap.DARK, outline=ap.LBLUE)
    for i, (s, color) in enumerate(PANEL[:shown]):
        ap.text(d, (40, 26 + 12 * i), s, color)


def main(out: str) -> int:
    work = Path(tempfile.mkdtemp(prefix="narr-"))
    clips = []
    for i, (anchor, text) in enumerate(LINES):
        path = work / f"line{i}.aiff"
        subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(path), text], check=True)
        clips.append((anchor, path, duration(path)))

    # Time warp: each line starts when the scene it describes starts. The scene never runs faster than
    # the silent video (1 / SLOW), slows down where a line needs longer, and stops on the gate for "checks".
    knots = [(0.0, 0.0)]
    starts, end = [], 0.0
    hold = None
    for anchor, path, dur in clips:
        scene = HOLD_AT if anchor == "checks" else anchor
        kv, kt = knots[-1]
        start = max(end + GAP, kv + (scene - kt) * ap.SLOW)
        starts.append(start)
        knots.append((start, scene))
        end = start + dur
        if anchor == "checks":
            hold = (start, start + dur + 0.6)
            knots.append((hold[1], scene))
            end = hold[1]
        print(f"{start:6.2f}s  scene {scene:5.1f}  {dur:5.2f}s  {path.name}")
    kv, kt = knots[-1]
    total = max(end + 1.5, kv + (ap.DURATION - kt) * ap.SLOW)
    knots.append((total, ap.DURATION - 0.01))
    checks_start, checks_end = hold

    def scene_at(v: float) -> float:
        for (v0, t0), (v1, t1) in zip(knots, knots[1:]):
            if v <= v1:
                return t0 + (t1 - t0) * ((v - v0) / (v1 - v0) if v1 > v0 else 1.0)
        return knots[-1][1]

    fps = ap.FPS
    size = (ap.W * ap.K * ap.UP, ap.H * ap.K * ap.UP)
    silent = work / "silent.mp4"
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    p = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", "-crf", "18", str(silent)], stdin=subprocess.PIPE)
    for i in range(int(total * fps)):
        v = i / fps
        img = ap.frame(scene_at(v))
        if checks_start <= v < checks_end:
            frac = (v - checks_start) / max(checks_end - checks_start - 0.6, 0.1)
            panel(img, max(1, min(len(PANEL), 1 + int(frac * (len(PANEL) - 1) * 1.05))))
        p.stdin.write(img.resize(size, Image.NEAREST).tobytes())
    p.stdin.close()
    p.wait()

    inputs, filters = ["-i", str(silent)], []
    for i, ((_, path, _), start) in enumerate(zip(clips, starts)):
        inputs += ["-i", str(path)]
        ms = int(start * 1000)
        filters.append(f"[{i + 1}:a]aresample=44100,adelay={ms}|{ms}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(clips)))
    filters.append(f"{mix}amix=inputs={len(clips)}:normalize=0,apad[aout]")
    subprocess.run([ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
                    "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-shortest", "-movflags", "+faststart", out], check=True)
    print(f"done: {out} ({total:.1f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
