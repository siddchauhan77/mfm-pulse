#!/usr/bin/env python3
"""
youtube_extractor.py — Pull metadata, transcripts, and comments from any public YouTube video.
No authentication required.

Usage:
    python3 youtube_extractor.py <youtube_url>

Or import and use the functions directly:
    from youtube_extractor import get_video_info, get_transcript, get_comments, get_everything
"""

import json
import re
import subprocess
import sys
import tempfile
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _run_ytdlp(*args):
    """Run yt-dlp with the given args and return stdout."""
    cmd = ["yt-dlp"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp error:\n{result.stderr.strip()}")
    return result.stdout


def _parse_vtt(vtt_text):
    """Convert a WebVTT subtitle file into plain text, deduplicating lines."""
    lines = []
    seen = set()
    for line in vtt_text.splitlines():
        line = line.strip()
        # Skip headers, timestamps, and blank lines
        if not line or line.startswith("WEBVTT") or "-->" in line or line.startswith("NOTE"):
            continue
        # Strip HTML tags (yt-dlp sometimes includes <c> tags)
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return " ".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_video_info(url: str) -> dict:
    """
    Return video metadata as a dict.

    Keys include: id, title, description, uploader, upload_date,
    duration, view_count, like_count, tags, categories, thumbnail, webpage_url
    """
    raw = _run_ytdlp("--dump-json", "--skip-download", "--no-playlist", url)
    data = json.loads(raw)

    return {
        "id":           data.get("id"),
        "title":        data.get("title"),
        "uploader":     data.get("uploader"),
        "upload_date":  data.get("upload_date"),         # YYYYMMDD string
        "duration_sec": data.get("duration"),
        "view_count":   data.get("view_count"),
        "like_count":   data.get("like_count"),
        "tags":         data.get("tags", []),
        "categories":   data.get("categories", []),
        "description":  data.get("description", ""),
        "thumbnail":    data.get("thumbnail"),
        "url":          data.get("webpage_url"),
    }


def get_transcript(url: str, lang: str = "en") -> str:
    """
    Return the video transcript as a plain text string.

    Tries manual captions first, falls back to auto-generated ones.
    Returns empty string if no captions exist.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Try manual subtitles first, then auto-generated
        for flags in [
            ["--write-subs",      "--sub-langs", lang, "--skip-download", "--no-playlist"],
            ["--write-auto-subs", "--sub-langs", lang, "--skip-download", "--no-playlist"],
        ]:
            try:
                _run_ytdlp(*flags, "--paths", tmpdir, "-o", "%(id)s.%(ext)s", url)
            except RuntimeError:
                continue

            # Find the downloaded .vtt file
            vtt_files = list(Path(tmpdir).glob("*.vtt"))
            if vtt_files:
                vtt_text = vtt_files[0].read_text(encoding="utf-8", errors="ignore")
                return _parse_vtt(vtt_text)

    return ""


def get_comments(url: str, max_comments: int = 100) -> list[dict]:
    """
    Return a list of comments as dicts.

    Each dict has: author, text, likes, timestamp (unix), is_reply
    Sorted by like count descending.
    """
    raw = _run_ytdlp(
        "--dump-json",
        "--skip-download",
        "--no-playlist",
        "--write-comments",
        "--extractor-args", f"youtube:max_comments={max_comments};comment_sort=top",
        url,
    )
    data = json.loads(raw)
    comments_raw = data.get("comments", [])

    comments = []
    for c in comments_raw:
        comments.append({
            "author":    c.get("author", ""),
            "text":      c.get("text", ""),
            "likes":     c.get("like_count", 0),
            "timestamp": c.get("timestamp"),
            "is_reply":  c.get("parent") != "root",
        })

    return sorted(comments, key=lambda x: x["likes"] or 0, reverse=True)


def get_everything(url: str, max_comments: int = 100, lang: str = "en") -> dict:
    """
    Convenience wrapper — returns metadata, transcript, and comments in one call.
    """
    info       = get_video_info(url)
    transcript = get_transcript(url, lang=lang)
    comments   = get_comments(url, max_comments=max_comments)

    return {
        "info":       info,
        "transcript": transcript,
        "comments":   comments,
    }


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------

def _fmt_duration(seconds):
    if not seconds:
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 youtube_extractor.py <youtube_url>")
        sys.exit(1)

    url = sys.argv[1]
    print(f"\nFetching data for: {url}\n{'='*60}")

    # --- Metadata ---
    print("\n[1/3] Fetching video info...")
    info = get_video_info(url)
    print(f"  Title:    {info['title']}")
    print(f"  Uploader: {info['uploader']}")
    print(f"  Date:     {info['upload_date']}")
    print(f"  Duration: {_fmt_duration(info['duration_sec'])}")
    print(f"  Views:    {info['view_count']:,}" if info['view_count'] else "  Views:    N/A")
    print(f"  Likes:    {info['like_count']:,}" if info['like_count'] else "  Likes:    N/A")
    if info['tags']:
        print(f"  Tags:     {', '.join(info['tags'][:8])}")

    # --- Transcript ---
    print("\n[2/3] Fetching transcript...")
    transcript = get_transcript(url)
    if transcript:
        preview = transcript[:500] + ("..." if len(transcript) > 500 else "")
        print(f"  Length:  {len(transcript):,} characters")
        print(f"  Preview: {preview}")
    else:
        print("  No transcript available for this video.")

    # --- Comments ---
    print("\n[3/3] Fetching top comments...")
    comments = get_comments(url, max_comments=50)
    if comments:
        print(f"  Total fetched: {len(comments)} comments")
        print("\n  Top 5 by likes:")
        for i, c in enumerate(comments[:5], 1):
            likes = f"{c['likes']:,}" if c['likes'] else "0"
            text_preview = c['text'][:120].replace("\n", " ")
            print(f"  {i}. [{likes} likes] {c['author']}: {text_preview}")
    else:
        print("  No comments fetched.")

    print(f"\n{'='*60}")
    print("Done. Import this file to use get_video_info(), get_transcript(), get_comments(), or get_everything().")


if __name__ == "__main__":
    main()
