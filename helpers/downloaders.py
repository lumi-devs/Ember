import os
import re
import uuid
from pathlib import Path
from typing import List, Optional, Tuple
import requests
import instaloader
from instagrapi import Client
from pytubefix import YouTube
import praw
import wget

# -------------------------------
# Global directories & session
# -------------------------------
DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

IG_USER = "***IG_USER_REMOVED***"
IG_PASS = "***IG_PASS_REMOVED***"
SESSION_FILE = Path("ig_settings.json")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"

# -------------------------------
# Helper Functions
# -------------------------------
def _create_temp_dir() -> Path:
    """Create a unique temporary folder for each download."""
    temp_dir = DOWNLOADS_DIR / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir

# -------------------------------
# YouTube Downloader
# -------------------------------
def download_youtube(url: str) -> Optional[str]:
    """Download YouTube video and return the local file path."""
    # Normalize Shorts URLs
    shorts_match = re.search(r"youtube\.com/shorts/([\w-]+)", url)
    if shorts_match:
        video_id = shorts_match.group(1)
        url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        yt = YouTube(url)
        stream = yt.streams.get_highest_resolution()
        out_file = stream.download(output_path=str(DOWNLOADS_DIR))
        print(f"✅ Downloaded YouTube video: {out_file}")
        return out_file
    except Exception as e:
        print(f"❌ Failed to download YouTube video: {e}")
        return None

# -------------------------------
# Instagram Downloader
# -------------------------------
from typing import List, Optional, Tuple
from pathlib import Path
import requests, wget
from instagrapi import Client

def download_instagram(url: str) -> Tuple[List[str], Optional[str]]:
    temp_dir = _create_temp_dir()
    files: List[str] = []
    caption: Optional[str] = None

    try:
        cl = Client()
        if Path("ig_settings.json").exists():
            cl.load_settings("ig_settings.json")
        cl.login(IG_USER, IG_PASS)
        cl.dump_settings("ig_settings.json")

        # Get media info (dict, not Media object)
        media_pk = cl.media_pk_from_url(url)
        data = cl.private_request(f"media/{media_pk}/info/")
        media = data["items"][0]

        # Extract caption safely
        caption = media.get("caption", {}).get("text", "")

        # Handle single photo or video
        if media.get("media_type") in [1, 2]:
            if media.get("media_type") == 2:  # video
                versions = media.get("video_versions", [])
                media_url = versions[0].get("url") if versions else None
                file_ext = ".mp4"
            else:  # image
                versions = media.get("image_versions2", {}).get("candidates", [])
                media_url = versions[0].get("url") if versions else None
                file_ext = ".jpg"

            if media_url:
                filename = temp_dir / f"{media.get('pk')}{file_ext}"
                r = requests.get(media_url, headers={"User-Agent": USER_AGENT}, timeout=20)
                r.raise_for_status()
                with open(filename, "wb") as f:
                    f.write(r.content)
                files.append(str(filename))

        # Handle album / carousel (media_type = 8)
        elif media.get("media_type") == 8:
            for i, item in enumerate(media.get("carousel_media", [])):
                if item.get("media_type") == 2:  # video
                    versions = item.get("video_versions", [])
                    media_url = versions[0].get("url") if versions else None
                    file_ext = ".mp4"
                else:  # image
                    versions = item.get("image_versions2", {}).get("candidates", [])
                    media_url = versions[0].get("url") if versions else None
                    file_ext = ".jpg"

                if media_url:
                    filename = temp_dir / f"{media.get('pk')}_{i}{file_ext}"
                    r = requests.get(media_url, headers={"User-Agent": USER_AGENT}, timeout=20)
                    r.raise_for_status()
                    with open(filename, "wb") as f:
                        f.write(r.content)
                    files.append(str(filename))

    except Exception as e:
        print(f"❌ Failed to download Instagram media: {e}")
    return files, caption

# -------------------------------
# Reddit Downloader
# -------------------------------
REDDIT_CLIENT_ID = "***REDDIT_ID_REMOVED***"
REDDIT_CLIENT_SECRET = "***REDDIT_SECRET_REMOVED***"
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=USER_AGENT
)

def download_reddit(url: str) -> Tuple[List[str], str, str]:
    """Download Reddit post media and return files + title + caption."""
    temp_dir = _create_temp_dir()
    files: List[str] = []
    title = ""
    caption = ""

    # Enhanced headers to mimic a real browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "Referer": "https://www.reddit.com/",
    }

    try:
        submission = reddit.submission(url=url)
        if submission.over_18:
            files=[]
            return
        title = submission.title.strip()
        caption = submission.selftext.strip()
        print(f"🔹 Fetched Reddit submission: {title} ({submission.id})")

        # Add the specific post URL as referer for media requests
        headers["Referer"] = url

        # Gallery
        if getattr(submission, 'is_gallery', False):
            for i, item in enumerate(submission.gallery_data['items']):
                media_id = item['media_id']
                metadata = submission.media_metadata[media_id]
                if metadata['e'] == 'Image':
                    # Get the highest quality image available
                    image_url = metadata['s']['u'].split("?")[0].replace("preview", "i")
                    filename = temp_dir / f"gallery_{i}_{uuid.uuid4().hex}.jpg"
                    
                    try:
                        print(image_url)
                        filename=wget.download(image_url, out=str(filename))
                        files.append(filename)
                        print(f"✅ Saved gallery image: {filename}")
                    except Exception as e:
                        print(f"❌ Failed to download gallery image {image_url}: {e}")

        # Video
        elif getattr(submission, 'is_video', False):
            try:
                video_url = submission.media['reddit_video']['fallback_url']
                filename = temp_dir / f"video_{uuid.uuid4().hex}.mp4"
                filename=wget.download(video_url, out=str(filename))
                files.append(filename)
                print(f"✅ Downloaded Reddit video: {filename}")
                
                # Try to get audio if available (Reddit separates audio and video)
                try:
                    if 'dash_audio' in submission.media['reddit_video']:
                        audio_url = submission.media['reddit_video']['dash_audio']['fallback_url']
                        audio_filename = temp_dir / f"audio_{uuid.uuid4().hex}.mp4"
                        files.append(filename)
                        audio_filename=wget.download(audio_url)
                        
                        # You could use ffmpeg here to merge video and audio
                        print(f"✅ Downloaded Reddit audio: {audio_filename}")
                except Exception as e:
                    print(f"⚠️ Could not download audio: {e}")
                    
            except Exception as e:
                print(f"❌ Failed to download Reddit video: {e}")

        # Direct media link
        elif getattr(submission, 'url', None):
            media_url = submission.url
            file_ext = os.path.splitext(media_url.split('?')[0])[1].lower()
            
            # Handle different file types
            if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.mp4']:
                filename = temp_dir / f"media_{uuid.uuid4().hex}{file_ext}"
                try:
                    filename=wget.download(media_url, out=str(filename))
                    files.append(str(filename))
                    print(f"✅ Downloaded Reddit media: {filename}")
                except Exception as e:
                    print(f"❌ Failed to download media {media_url}: {e}")
            
            # Handle gifv (which is actually mp4)
            elif file_ext == '.gifv':
                # Convert to mp4
                mp4_url = media_url.replace('.gifv', '.mp4')
                filename = temp_dir / f"media_{uuid.uuid4().hex}.mp4"
                try:
                    filename=wget.download(mp4_url, out=str(filename))
                    files.append(str(filename))
                    print(f"✅ Downloaded Reddit GIFV as MP4: {filename}")
                except Exception as e:
                    print(f"❌ Failed to download GIFV {mp4_url}: {e}")

        else:
            print("⚠️ Media type not supported or no media found")

    except Exception as e:
        print(f"❌ Failed to fetch Reddit post: {e}")

    return files, title, caption
