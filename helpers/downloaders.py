import os
import re
import uuid
import shutil
from pathlib import Path
import praw
import instaloader
import requests
from pytubefix import YouTube

# -------------------------------
# Global directories & session
# -------------------------------
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

IG_USERNAME = os.environ.get("IG_USER")
IG_PASSWORD = os.environ.get("IG_PASS")
SESSION_FILE = Path("session.json")

# -------------------------------
# Helper functions
# -------------------------------
def _create_temp_dir() -> Path:
    """Create a unique temporary folder for each download."""
    temp_dir = DOWNLOADS_DIR / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

# -------------------------------
# YouTube Downloader
# -------------------------------
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
        out_file = stream.download(output_path=str(DOWNLOADS_DIR))
        return out_file
    except Exception as e:
        print(f"❌ Failed to download YouTube video: {e}")
        return None

# -------------------------------
# Instagram Downloader
# -------------------------------
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

    # Optional login for private reels
    if IG_USERNAME and IG_PASSWORD:
        try:
            L.load_session_from_file(IG_USERNAME, str(SESSION_FILE))
        except FileNotFoundError:
            L.login(IG_USERNAME, IG_PASSWORD)
            L.save_session_to_file(SESSION_FILE)

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

# -------------------------------
# Reddit Downloader
# ------------------------------
REDDIT_CLIENT_ID = "***REDDIT_ID_REMOVED***"
REDDIT_CLIENT_SECRET = "***REDDIT_SECRET_REMOVED***"
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent="DiscordBot/1.0"
)


def download_reddit(url: str):
    temp_dir = DOWNLOADS_DIR / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    files = []
    caption = ""

    print(f"🔹 Starting download for Reddit URL: {url}")
    try:
        submission = reddit.submission(url=url)
        print(f"🔹 Fetched submission: {submission.title} ({submission.id})")

        caption = f"{submission.title}\n\n{submission.selftext}".strip()
        print(f"🔹 Caption length: {len(caption)} chars")

        # Detect media type
        media_url = submission.url
        print(f"🔹 Submission URL: {media_url}")

        if getattr(submission, "is_video", False):
           reddit_video_url = submission.media["reddit_video"]["fallback_url"]
           filename = temp_dir / reddit_video_url.split("/")[-1].split("?")[0]
           resp = requests.get(reddit_video_url, headers={"User-Agent": REDDIT_USER_AGENT})
           resp.raise_for_status()
           with open(filename, "wb") as f:
               f.write(resp.content)
           files.append(str(filename))
           print(f"✅ Downloaded Reddit video: {filename}")

        elif media_url.endswith((".jpg", ".jpeg", ".png", ".gif", ".mp4")):
             filename = temp_dir / media_url.split("/")[-1].split("?")[0]
             resp = requests.get(media_url, headers={"User-Agent": REDDIT_USER_AGENT})
             resp.raise_for_status()
             with open(filename, "wb") as f:
                 f.write(resp.content)
             files.append(str(filename))
             print(f"✅ Downloaded Reddit media: {filename}")

        else:
            print("⚠️ Media type not supported or no media found")

    except Exception as e:
        print(f"❌ Failed to fetch Reddit post: {e}")

    print(f"🔹 Returning {len(files)} files, caption length {len(caption)}")
    return files, caption