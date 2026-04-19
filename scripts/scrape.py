#!/usr/bin/env python3
"""
MFM Pulse — YouTube Comment Scraper (yt-dlp edition)
No API key required. Uses the local yt-dlp install.

Run: python3 scripts/scrape.py

What it does:
  1. Pulls video list from @MyFirstMillionPod via yt-dlp flat-playlist dump
  2. Sorts by view count, takes top MAX_VIDEOS
  3. Fetches up to MAX_COMMENTS comments per video via yt-dlp
  4. Saves raw data to data/raw-comments.json

Requirements:
  - yt-dlp installed (already at /Library/Frameworks/Python.framework/Versions/3.13/bin/yt-dlp)
  - Run from the project root: python3 scripts/scrape.py
"""

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

CHANNEL_URL  = "https://www.youtube.com/@MyFirstMillionPod"
MAX_VIDEOS   = 50    # pull list of 200, keep top 50 by view count
LIST_LIMIT   = 200   # how many videos to scan from channel history
MAX_COMMENTS = 100   # comments per video (yt-dlp top-sorted)
DELAY_SEC    = 1.5   # polite delay between video requests

# ─── Import our extractor helper ─────────────────────────────────────────────

scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))
from youtube_extractor import get_comments, get_video_info  # noqa: E402

# ─── Helpers ─────────────────────────────────────────────────────────────────

def run(*args):
    """Run yt-dlp and return stdout lines."""
    cmd = ["yt-dlp"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp error:\n{result.stderr.strip()}")
    return result.stdout


def get_channel_videos(limit: int = LIST_LIMIT) -> list[dict]:
    """
    Pull video list from the MFM channel using flat-playlist dump.
    Returns list of dicts with id, title, url, view_count.
    """
    print(f"  Fetching channel video list (up to {limit} videos)...")
    raw = run(
        "--flat-playlist",
        "--dump-json",
        f"--playlist-end", str(limit),
        CHANNEL_URL,
    )

    videos = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        video_id = item.get("id") or item.get("url", "").split("v=")[-1]
        if not video_id:
            continue

        videos.append({
            "id":         video_id,
            "title":      item.get("title", ""),
            "url":        f"https://www.youtube.com/watch?v={video_id}",
            "view_count": item.get("view_count") or 0,
            "duration":   item.get("duration") or 0,
        })

    return videos


def enrich_video(video: dict) -> dict:
    """
    If view_count is missing from flat dump, fetch full video info.
    Only called when needed — most modern yt-dlp builds include view counts.
    """
    try:
        info = get_video_info(video["url"])
        video["view_count"]   = info.get("view_count") or 0
        video["like_count"]   = info.get("like_count") or 0
        video["thumbnail"]    = info.get("thumbnail") or ""
        video["upload_date"]  = info.get("upload_date") or ""
        video["comment_count"] = 0  # filled in later from scraped comments
    except Exception as e:
        print(f"    ⚠ Could not enrich {video['id']}: {e}")
    return video


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🎙️  MFM Pulse — YouTube Comment Scraper (yt-dlp)")
    print(f"   Channel: {CHANNEL_URL}")
    print()

    # 1. Get video list
    print("1. Getting channel video list...")
    try:
        videos = get_channel_videos()
    except RuntimeError as e:
        print(f"❌ Failed to get video list:\n{e}")
        sys.exit(1)

    print(f"   Got {len(videos)} videos from channel")

    # 2. If view counts are missing, enrich the first batch
    missing_views = [v for v in videos if v["view_count"] == 0]
    if len(missing_views) > len(videos) * 0.5:
        print("   ⚠ View counts missing from flat dump — fetching full info for top candidates...")
        # Only enrich first 80 (sorted by position = recency) to find top by views
        for i, v in enumerate(videos[:80]):
            if v["view_count"] == 0:
                print(f"   Enriching [{i+1}/80] {v['title'][:50]}...")
                enrich_video(v)
                time.sleep(0.5)

    # 3. Sort by view count, take top MAX_VIDEOS
    top_videos = sorted(videos, key=lambda v: v["view_count"], reverse=True)[:MAX_VIDEOS]
    print(f"   Top video: \"{top_videos[0]['title'][:60]}\" — {top_videos[0]['view_count']:,} views")

    # 4. Fetch comments for each
    print(f"\n2. Fetching comments for {len(top_videos)} videos...")
    raw_data = []
    total_comments = 0

    for i, video in enumerate(top_videos):
        title_short = video["title"][:55]
        print(f"   [{i+1:02d}/{len(top_videos)}] {title_short}...", end="", flush=True)

        # Get full info if we don't have thumbnail/upload_date yet
        if not video.get("thumbnail"):
            try:
                info = get_video_info(video["url"])
                video["thumbnail"]    = info.get("thumbnail", "")
                video["like_count"]   = info.get("like_count", 0)
                video["upload_date"]  = info.get("upload_date", "")
                if info.get("view_count"):
                    video["view_count"] = info["view_count"]
            except Exception:
                video.setdefault("thumbnail", "")
                video.setdefault("like_count", 0)
                video.setdefault("upload_date", "")

        # Fetch comments
        try:
            comments_raw = get_comments(video["url"], max_comments=MAX_COMMENTS)
            # Normalize to the format analyze.ts expects
            comments = [
                {
                    "text":        c.get("text", ""),
                    "likeCount":   c.get("likes", 0) or 0,
                    "publishedAt": str(c.get("timestamp", "")) if c.get("timestamp") else "",
                }
                for c in comments_raw
                if c.get("text", "").strip()
            ]
        except Exception as e:
            print(f" ⚠ comments unavailable ({e})")
            comments = []

        total_comments += len(comments)
        print(f" ({len(comments)} comments)")

        # Parse upload_date (YYYYMMDD → ISO)
        upload_date_raw = video.get("upload_date", "")
        if len(upload_date_raw) == 8:
            published_at = f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}T00:00:00Z"
        else:
            published_at = upload_date_raw or ""

        raw_data.append({
            "id":           video["id"],
            "title":        video["title"],
            "publishedAt":  published_at,
            "viewCount":    video.get("view_count", 0),
            "likeCount":    video.get("like_count", 0),
            "commentCount": len(comments),
            "thumbnail":    video.get("thumbnail", ""),
            "url":          video["url"],
            "comments":     comments,
        })

        # Polite delay
        time.sleep(DELAY_SEC)

    # 5. Save
    output = {
        "scrapedAt":     datetime.utcnow().isoformat() + "Z",
        "channelUrl":    CHANNEL_URL,
        "videoCount":    len(raw_data),
        "totalComments": total_comments,
        "videos":        raw_data,
    }

    out_path = Path(__file__).parent.parent / "data" / "raw-comments.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    print()
    print(f"✅ Done! {len(raw_data)} videos, {total_comments:,} comments")
    print(f"   Saved to: data/raw-comments.json")
    print(f"   Next step: npm run analyze")


if __name__ == "__main__":
    main()
