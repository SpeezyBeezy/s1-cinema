#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Batch 480p Anime Converter - GPU decode fallback + AAC 160k audio for GT-I9000
- Hybrid: try GPU decode if available, CPU encode with libx264
- Converts audio to AAC 160kbps stereo
- Copies subtitles and chapters; attachments optional (disabled by default)
- Preserves last input folder under output root
- Uses system temp folder for intermediate files
- "_480p" appended to output filenames, MKV container
- Ensures width/height are even for H.264
"""

from pathlib import Path
import subprocess, os, shutil, tempfile, json, uuid, sys
from typing import List, Tuple, Optional

CRF = 18
PRESET = "medium"
TUNE_ANIMATION = True
LEVEL = "3.0"
OVERWRITE = False
SKIP_IF_ALREADY_480P = True

# Toggle: whether to copy container attachments (fonts, etc).
# Default False because attachments sometimes cause muxing failures with hwaccel / re-encode.
COPY_ATTACHMENTS = True

# Increase mux queue size to reduce "muxer queue" related failures
MAX_MUXING_QUEUE_SIZE = "4096"

VIDEO_EXTS = {".mkv", ".mp4", ".webm", ".webem", ".mov", ".m4v", ".avi", ".flv", ".ts", ".m2ts", ".mts", ".mxf", ".dat"}

def prompt_input_dirs() -> List[Path]:
    print("Enter input directories (blank = current directory):")
    inputs, first = [], True
    while True:
        try:
            line = input().strip().strip('"').strip("'")
        except EOFError:
            break
        if not line:
            if first:
                return [Path.cwd()]
            break
        first = False
        p = Path(line).expanduser()
        if p.exists() and p.is_dir():
            inputs.append(p)
    return inputs or [Path.cwd()]

def prompt_output_dir() -> Path:
    out = input("Enter OUTPUT directory (blank = current directory): ").strip().strip('"').strip("'")
    outp = Path(out).expanduser() if out else Path.cwd()
    outp.mkdir(parents=True, exist_ok=True)
    return outp

def is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS

def collect_video_files(roots: List[Path]) -> List[Tuple[Path, Path]]:
    files = []
    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                p = Path(dirpath) / fn
                if is_video_file(p):
                    files.append((root, p))
    return files

def base_with_suffix(base: str, suffix: str) -> str:
    return base if base.endswith(suffix) else base + suffix

def make_output_path(in_file: Path, in_root: Path, out_root: Path) -> Path:
    rel = in_file.relative_to(in_root)
    out_dir = out_root / in_root.name / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{base_with_suffix(rel.stem,'_480p')}.mkv"

def ffprobe_info(path: Path) -> dict:
    cmd = ["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height,pix_fmt,codec_name","-of","json",str(path)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()
        data = json.loads(out)
        streams = data.get("streams") or []
        return streams[0] if streams else {}
    except Exception:
        return {}

def already_satisfies_480p(info: dict) -> bool:
    if not info:
        return False
    w = info.get("width")
    h = info.get("height")
    pix = (info.get("pix_fmt") or "").lower()
    codec = (info.get("codec_name") or "").lower()
    return w is not None and h is not None and w <= 854 and h <= 480 and "h264" in codec and pix in ("yuv420p","yuvj420p")

def detect_hwaccel() -> Optional[str]:
    """
    Detect available ffmpeg hwaccels and pick a preferred one for this platform.
    Returns the hwaccel name (e.g. 'd3d11va','dxva2','vaapi','qsv','cuda','nvdec','cuvid','amf') or None.
    """
    try:
        out = subprocess.check_output(["ffmpeg","-hide_banner","-hwaccels"], stderr=subprocess.STDOUT).decode(errors="ignore").splitlines()
        found = [ln.strip().split()[0].lower() for ln in out if ln.strip() and not ln.lower().startswith(("hardware","available"))]
        if sys.platform.startswith("win"):
            pref = ("d3d11va","dxva2","qsv","cuvid","nvdec","cuda","amf","vaapi")
        else:
            pref = ("vaapi","qsv","cuvid","nvdec","cuda","d3d11va","dxva2","amf")
        for p in pref:
            if p in found:
                return p
    except Exception:
        pass
    return None

HWACCEL = detect_hwaccel()
TEMP_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

def build_ffmpeg_command(in_file: Path, out_file: Path, hwaccel: Optional[str]) -> List[str]:
    # Scale while preserving aspect ratio and ensuring even width/height
    vf_chain = "scale=trunc(iw*min(854/iw\\,480/ih)/2)*2:trunc(ih*min(854/iw\\,480/ih)/2)*2,setsar=1"
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-stats"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-i", str(in_file), "-map", "0", "-map_metadata", "0", "-map_chapters", "0"]
    # Video: encode x264 baseline for GT-I9000 compatibility
    cmd += ["-c:v", "libx264", "-profile:v", "baseline", "-level:v", LEVEL, "-pix_fmt", "yuv420p", "-vf", vf_chain, "-preset", PRESET, "-crf", str(CRF)]
    if TUNE_ANIMATION:
        cmd += ["-tune", "animation"]
    # Audio: transcode to AAC 160kbps stereo for phone compatibility
    cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    # subtitles: copy
    cmd += ["-c:s", "copy"]
    # attachments: optional
    if COPY_ATTACHMENTS:
        cmd += ["-c:t", "copy"]
    # max mux queue / threads / overwrite
    cmd += ["-max_muxing_queue_size", MAX_MUXING_QUEUE_SIZE, "-threads", "0", "-n" if not OVERWRITE else "-y", str(out_file)]
    return cmd

def run_cmd(cmd: List[str]) -> int:
    p = None
    try:
        p = subprocess.Popen(cmd)
        return p.wait()
    except KeyboardInterrupt:
        try:
            if p:
                p.terminate()
        except Exception:
            pass
        return 130
    except Exception:
        # Non-ffmpeg errors: return non-zero
        return 1

def convert_one(in_root: Path, in_file: Path, out_root: Path) -> Tuple[Path, Path, str]:
    out_file = make_output_path(in_file, in_root, out_root)
    if out_file.exists() and not OVERWRITE:
        return (in_file, out_file, "exists-skip")

    # If already satisfies 480p H.264 baseline/yuv420p we avoid re-encoding video,
    # but we STILL convert audio to AAC 160k for GT-I9000 compatibility.
    if SKIP_IF_ALREADY_480P:
        info = ffprobe_info(in_file)
        if already_satisfies_480p(info):
            temp_name = TEMP_DIR / f"{uuid.uuid4().hex}.tmp.mkv"
            cmd = [
                "ffmpeg","-hide_banner","-loglevel","error","-stats","-i", str(in_file),
                "-map","0","-map_metadata","0","-map_chapters","0",
                "-c:v","copy",                    # keep existing H.264 video
                "-c:a","aac","-b:a","160k","-ac","2",  # ensure AAC 160k audio
                "-c:s","copy",
            ]
            if COPY_ATTACHMENTS:
                cmd += ["-c:t","copy"]
            cmd += ["-max_muxing_queue_size", MAX_MUXING_QUEUE_SIZE, "-threads", "0", "-n" if not OVERWRITE else "-y", str(temp_name)]
            rc = run_cmd(cmd)
            if rc == 0:
                shutil.move(str(temp_name), out_file)
                return (in_file, out_file, "remuxed-audio-converted")
            if temp_name.exists():
                temp_name.unlink()
            # if remux failed, fall through to full encode

    # Full encode path (video encode + audio aac)
    temp_name = TEMP_DIR / f"{uuid.uuid4().hex}.tmp.mkv"

    # Try with detected hwaccel (if any). If it fails, retry without hwaccel.
    tried_hw = HWACCEL
    if tried_hw:
        rc = run_cmd(build_ffmpeg_command(in_file, temp_name, tried_hw))
        if rc != 0:
            if temp_name.exists():
                temp_name.unlink()
            rc = run_cmd(build_ffmpeg_command(in_file, temp_name, None))
    else:
        rc = run_cmd(build_ffmpeg_command(in_file, temp_name, None))

    if rc == 0:
        shutil.move(str(temp_name), out_file)
        return (in_file, out_file, "encoded")
    if temp_name.exists():
        temp_name.unlink()
    return (in_file, out_file, f"error({rc})")

def main():
    inputs = prompt_input_dirs()
    outdir = prompt_output_dir()
    tasks = collect_video_files(inputs)
    if not tasks:
        print("No video files found.")
        return
    total, done, skipped, remuxed, errors = len(tasks), 0, 0, 0, 0
    print(f"Detected hwaccel: {HWACCEL or 'none'}")
    print(f"COPY_ATTACHMENTS = {COPY_ATTACHMENTS}; MAX_MUXING_QUEUE_SIZE = {MAX_MUXING_QUEUE_SIZE}")
    for idx, (root, f) in enumerate(tasks, 1):
        print(f"[{idx}/{total}] {f.relative_to(root)}")
        try:
            _, out_p, status = convert_one(root, f, outdir)
            if status == "encoded":
                done += 1
                print(f"  ✔ Encoded -> {out_p}")
            elif status == "remuxed-audio-converted":
                remuxed += 1
                print(f"  ✔ Already 480p; remuxed (audio->AAC160) -> {out_p}")
            elif status == "exists-skip":
                skipped += 1
                print(f"  ↷ Exists, skipped -> {out_p}")
            else:
                errors += 1
                print(f"  ✖ {status} -> {out_p}")
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        except Exception as e:
            errors += 1
            print(f"  ✖ Unexpected error: {e}")
    print(f"\nSummary: Encoded={done}, Remuxed={remuxed}, Skipped={skipped}, Errors={errors}")

if __name__ == "__main__":
    main()
