import os
from pathlib import Path
import shutil
import uuid
import instaloader
import requests

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

IG_USERNAME = os.environ.get("IG_USER")
IG_PASSWORD = os.environ.get("IG_PASS")
SESSION_FILE = Path("session.json")

def _create_temp_dir() -> Path:
    """Create a unique temporary folder for each download."""
    temp_dir = DOWNLOADS_DIR / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

import re
import os
from pytubefix import YouTube

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

def download_youtube(url: str) -> str:
    """
    Download YouTube video (including shorts) using pytubefix and return filepath.
    """
    # Normalize Shorts URLs
    shorts_match = re.search(r"youtube\.com/shorts/([\w-]+)", url)
    if shorts_match:
        video_id = shorts_match.group(1)
        url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        yt = YouTube(url)
        stream = yt.streams.get_highest_resolution()
        out_file = stream.download(output_path=DOWNLOADS_DIR)
        return out_file
    except Exception as e:
        print(f"❌ Failed to download YouTube video: {e}")
        return None



def download_instagram(url: str) -> list[str]:
    temp_dir = _create_temp_dir()
    L = instaloader.Instaloader(
        dirname_pattern=str(temp_dir),
        download_videos=True,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern=""
    )

    # Optional login
    if SESSION_FILE.exists():
        L.load_session_from_file(IG_USERNAME, str(SESSION_FILE))

    target_files = []
    try:
        shortcode = url.rstrip("/").split("/")[-1]
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        L.download_post(post, target=temp_dir)
        for f in temp_dir.iterdir():
            if f.is_file() and f.suffix.lower() in [".jpg", ".mp4", ".png"]:
                target_files.append(str(f))
        return target_files
    except Exception as e:
        print("❌ Failed to download Instagram media:", e)
        return []

def download_reddit(url: str):
    """Download Reddit media and return files + formatted caption"""
    import requests
    import os
    from pathlib import Path
    import uuid

    temp_dir = Path("downloads") / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    headers = {"User-Agent": "DiscordBot"}
    r = requests.get(url + ".json", headers=headers)
    r.raise_for_status()
    data = r.json()[0]["data"]["children"][0]["data"]

    # Media
    media_url = None
    if "url_overridden_by_dest" in data:
        media_url = data["url_overridden_by_dest"]
    elif "media" in data and data["media"] and "reddit_video" in data["media"]:
        media_url = data["media"]["reddit_video"]["fallback_url"]

    files = []
    if media_url:
        filename = temp_dir / media_url.split("/")[-1].split("?")[0]
        resp = requests.get(media_url, headers=headers)
        with open(filename, "wb") as f:
            f.write(resp.content)
        files.append(str(filename))

    # Format caption as: {title}\n\n{selftext}
    title = data.get("title") or ""
    selftext = data.get("selftext") or ""
    caption = f"{title}\n\n{selftext}".strip()

    return files, caption
