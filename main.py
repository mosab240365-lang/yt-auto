"""
YouTube automation: script -> voice-over (gTTS) -> MP4 (moviepy) -> optional upload.

Run locally:      python main.py
Skip upload:      python main.py --no-upload
Env vars for upload (store them as GitHub Actions secrets):
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional:
    YT_PRIVACY_STATUS   private | unlisted | public   (default: private)
    YT_CATEGORY_ID      YouTube category id            (default: 27 = Education)
"""

import argparse
import datetime as dt
import os
import random
import sys
import textwrap
from pathlib import Path

import numpy as np
from gtts import gTTS
from moviepy import AudioFileClip, ImageClip, concatenate_videoclips
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = Path("output")
WIDTH, HEIGHT = 1280, 720
FPS = 24

# --------------------------------------------------------------------------- #
# 1. Mock script
# --------------------------------------------------------------------------- #
MOCK_SCRIPTS = [
    {
        "title": "3 Quick Facts About the Ocean",
        "scenes": [
            "Welcome back! Here are three quick facts about the ocean.",
            "Fact one: the ocean covers about seventy one percent of Earth's surface.",
            "Fact two: more than eighty percent of the ocean has never been explored.",
            "Fact three: the ocean produces over half of the oxygen we breathe.",
            "Thanks for watching. Subscribe for a new fact video every day!",
        ],
    },
    {
        "title": "3 Quick Facts About Space",
        "scenes": [
            "Welcome back! Here are three quick facts about space.",
            "Fact one: a day on Venus is longer than its year.",
            "Fact two: one million Earths could fit inside the Sun.",
            "Fact three: footprints on the Moon could last for millions of years.",
            "Thanks for watching. Subscribe for a new fact video every day!",
        ],
    },
    {
        "title": "3 Quick Facts About Honey Bees",
        "scenes": [
            "Welcome back! Here are three quick facts about honey bees.",
            "Fact one: a honey bee visits up to five thousand flowers in a single day.",
            "Fact two: bees communicate by doing a special waggle dance.",
            "Fact three: honey never spoils if it is stored properly.",
            "Thanks for watching. Subscribe for a new fact video every day!",
        ],
    },
]


def get_script() -> dict:
    """Pick a script. Replace this with an AI/API call later if you like."""
    # Seed by date so a re-run on the same day produces the same video.
    random.seed(dt.date.today().isoformat())
    return random.choice(MOCK_SCRIPTS)


# --------------------------------------------------------------------------- #
# 2. Text-to-speech
# --------------------------------------------------------------------------- #
def text_to_speech(text: str, path: Path, lang: str = "en") -> Path:
    gTTS(text=text, lang=lang).save(str(path))
    return path


# --------------------------------------------------------------------------- #
# 3. Frame rendering (Pillow, so no ImageMagick is needed)
# --------------------------------------------------------------------------- #
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Ubuntu runners
    "/Library/Fonts/Arial Bold.ttf",                          # macOS
    "C:/Windows/Fonts/arialbd.ttf",                           # Windows
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)  # Pillow >= 10.1


def render_frame(text: str, index: int, total: int, title: str) -> np.ndarray:
    # Simple vertical gradient background whose hue shifts per scene.
    top = np.array([20, 30 + index * 15, 80 + index * 20]) % 256
    bottom = np.array([5, 5, 25])
    t = np.linspace(0, 1, HEIGHT)[:, None]
    gradient = (top * (1 - t) + bottom * t).astype(np.uint8)
    img = Image.fromarray(np.repeat(gradient[:, None, :], WIDTH, axis=1).reshape(HEIGHT, WIDTH, 3))

    draw = ImageDraw.Draw(img)
    body_font = load_font(54)
    small_font = load_font(28)

    lines = textwrap.wrap(text, width=32)
    line_h = body_font.getbbox("Ag")[3] + 18
    y = (HEIGHT - line_h * len(lines)) // 2
    for line in lines:
        w = draw.textlength(line, font=body_font)
        draw.text(((WIDTH - w) / 2, y), line, font=body_font, fill="white",
                  stroke_width=3, stroke_fill="black")
        y += line_h

    draw.text((40, 30), title, font=small_font, fill=(220, 220, 220))
    draw.text((WIDTH - 120, HEIGHT - 60), f"{index + 1}/{total}", font=small_font,
              fill=(200, 200, 200))
    return np.array(img)


# --------------------------------------------------------------------------- #
# 4. Build the video
# --------------------------------------------------------------------------- #
def build_video(script: dict, out_path: Path) -> Path:
    work = OUTPUT_DIR / "tmp"
    work.mkdir(parents=True, exist_ok=True)

    clips = []
    scenes = script["scenes"]
    for i, sentence in enumerate(scenes):
        print(f"  scene {i + 1}/{len(scenes)}: {sentence[:50]}...")
        audio = AudioFileClip(str(text_to_speech(sentence, work / f"scene_{i}.mp3")))
        frame = render_frame(sentence, i, len(scenes), script["title"])
        clip = ImageClip(frame).with_duration(audio.duration + 0.4).with_audio(audio)
        clips.append(clip)

    video = concatenate_videoclips(clips, method="compose")
    video.write_videofile(str(out_path), fps=FPS, codec="libx264", audio_codec="aac",
                          logger=None)
    video.close()
    for c in clips:
        c.close()
    return out_path


# --------------------------------------------------------------------------- #
# 5. Upload to YouTube (YouTube Data API v3)
# --------------------------------------------------------------------------- #
def upload_to_youtube(video_path: Path, script: dict) -> str | None:
    client_id = os.getenv("YT_CLIENT_ID")
    client_secret = os.getenv("YT_CLIENT_SECRET")
    refresh_token = os.getenv("YT_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        print("YouTube credentials not set - skipping upload.")
        return None

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body = {
        "snippet": {
            "title": script["title"],
            "description": "\n".join(script["scenes"]) + "\n\n#facts #shorts",
            "tags": ["facts", "education", "daily facts"],
            "categoryId": os.getenv("YT_CATEGORY_ID", "27"),
        },
        "status": {
            "privacyStatus": os.getenv("YT_PRIVACY_STATUS", "private"),
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  upload {int(status.progress() * 100)}%")
    video_id = response["id"]
    print(f"Uploaded: https://youtu.be/{video_id}")
    return video_id


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Generate (and upload) a daily video.")
    parser.add_argument("--no-upload", action="store_true", help="only create the MP4")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    script = get_script()
    out_path = OUTPUT_DIR / f"video_{dt.date.today().isoformat()}.mp4"

    print(f"Building video: {script['title']}")
    build_video(script, out_path)
    print(f"Saved {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")

    if not args.no_upload:
        upload_to_youtube(out_path, script)
    return 0


if __name__ == "__main__":
    sys.exit(main())
