"""
Assemble a step-sequence animated GIF from captured screenshots (dev/docs tool).

    python -m tools.make_gif OUT.gif frame1.png frame2.png ... [--width 900] [--duration 1400]

Frames are downscaled to a common width and written as an optimized looping GIF. Used to build the
guided walkthroughs under static/docs/ for the in-app Help page.
"""

import argparse

from PIL import Image


def make_gif(out_path, frame_paths, width=900, duration=1400):
    frames = []
    for p in frame_paths:
        im = Image.open(p).convert("RGB")
        w, h = im.size
        im = im.resize((width, round(h * width / w)), Image.Resampling.LANCZOS)
        frames.append(im)
    if not frames:
        raise SystemExit("no frames")
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, optimize=True)
    print(f"wrote {out_path} ({len(frames)} frames, {width}px wide)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("frames", nargs="+")
    ap.add_argument("--width", type=int, default=900)
    ap.add_argument("--duration", type=int, default=1400)
    args = ap.parse_args()
    make_gif(args.out, args.frames, width=args.width, duration=args.duration)
