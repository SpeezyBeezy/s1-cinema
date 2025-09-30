# s1-cinema
a batch video converter designed to make anime (and other videos) run smoothly on the Samsung Galaxy S GT-I9000 (2010) using MX Player.

---

## Features

- Video is re-encoded to H.264 Baseline Profile, Level 3.0, with a maximum resolution of 480p.  
- Audio is converted to AAC stereo at 160 kbps.  
- Subtitles and chapters are preserved automatically.  
- Container attachments (fonts, etc.) are optionally preserved.  
- Uses hardware acceleration for decoding if available, with CPU fallback.  
- If a video already meets Galaxy S1 requirements (≤480p H.264 baseline + yuv420p), only the audio is remuxed to save time.  
- Recursively scans input folders, preserves directory structure, and appends `_480p` to output filenames.

This setup ensures smooth playback on MX Player for Galaxy S1.

---

## Requirements

- Python 3.10+ (uses standard library only)  
- FFmpeg installed and accessible via your system PATH  

No additional Python packages are required.

---

designed for MX Player 1.7.40 (arm-v7a)
