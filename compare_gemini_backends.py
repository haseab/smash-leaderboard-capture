#!/usr/bin/env python3
"""
Compare direct Gemini SDK results against the Cloudflare Worker proxy.

Drop real result-screen videos into testvideos/ and run:

    python compare_gemini_backends.py

Required environment:
    GEMINI_API_KEY       - direct SDK baseline
    GEMINI_PROXY_URL     - Worker /analyze URL
    GEMINI_PROXY_TOKEN   - Worker client token
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from gemini_match_analyzer import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_PROXY_TIMEOUT_SECONDS,
    GeminiProxyClient,
    PlayerStats,
    analyze_match_results_video,
    get_gemini_api_version,
    get_gemini_model,
)


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
CONTEXT_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def create_direct_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required for the direct Gemini baseline")

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version=get_gemini_api_version()),
    )


def create_proxy_gemini_client() -> GeminiProxyClient:
    proxy_url = os.getenv("GEMINI_PROXY_URL", "").strip()
    proxy_token = os.getenv("GEMINI_PROXY_TOKEN", "").strip()
    if not proxy_url:
        raise ValueError("GEMINI_PROXY_URL is required for the Worker proxy comparison")
    if not proxy_token:
        raise ValueError("GEMINI_PROXY_TOKEN is required for the Worker proxy comparison")

    return GeminiProxyClient(
        proxy_url=proxy_url,
        auth_token=proxy_token,
        timeout_seconds=env_int("GEMINI_PROXY_TIMEOUT_SECONDS", DEFAULT_GEMINI_PROXY_TIMEOUT_SECONDS),
    )


def env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def discover_videos(videos_dir: Path) -> List[Path]:
    if not videos_dir.exists():
        return []

    return sorted(
        path
        for path in videos_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def find_context_image(video_path: Path) -> Optional[Path]:
    for extension in CONTEXT_IMAGE_EXTENSIONS:
        candidate = video_path.with_suffix(extension)
        if candidate.exists():
            return candidate

    for name in ("context", "frame_42", "player_context"):
        for extension in CONTEXT_IMAGE_EXTENSIONS:
            candidate = video_path.parent / f"{name}{extension}"
            if candidate.exists():
                return candidate

    return None


def serialize_stats(stats: Optional[List[PlayerStats]]) -> Optional[List[Dict[str, object]]]:
    if stats is None:
        return None

    return [stat.model_dump() for stat in stats]


def canonicalize_stats(
    stats: Optional[List[Dict[str, object]]],
    *,
    ignore_player_order: bool,
) -> Optional[List[Dict[str, object]]]:
    if stats is None:
        return None

    canonical = [dict(player) for player in stats]
    if ignore_player_order:
        canonical.sort(
            key=lambda player: (
                str(player.get("player_name", "")),
                str(player.get("smash_character", "")),
                int(player.get("total_kos", 0)),
                int(player.get("total_falls", 0)),
                int(player.get("total_sds", 0)),
            )
        )
    return canonical


def run_backend(
    *,
    backend_name: str,
    client,
    video_path: Path,
    context_image_path: Optional[Path],
    slowdown_factor: int,
    model: str,
) -> Dict[str, object]:
    print(f"  {backend_name}: analyzing {video_path.name}...")
    try:
        stats = analyze_match_results_video(
            client,
            str(video_path),
            context_image_path=str(context_image_path) if context_image_path else None,
            slowdown_factor=slowdown_factor,
            model=model,
        )
        return {
            "ok": True,
            "stats": serialize_stats(stats),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        }


def compare_video(
    *,
    video_path: Path,
    direct_client,
    proxy_client,
    slowdown_factor: int,
    model: str,
    ignore_player_order: bool,
) -> Dict[str, object]:
    context_image_path = find_context_image(video_path)
    if context_image_path:
        print(f"  context image: {context_image_path.name}")

    direct_result = run_backend(
        backend_name="direct",
        client=direct_client,
        video_path=video_path,
        context_image_path=context_image_path,
        slowdown_factor=slowdown_factor,
        model=model,
    )
    proxy_result = run_backend(
        backend_name="proxy",
        client=proxy_client,
        video_path=video_path,
        context_image_path=context_image_path,
        slowdown_factor=slowdown_factor,
        model=model,
    )

    direct_canonical = canonicalize_stats(
        direct_result.get("stats"),
        ignore_player_order=ignore_player_order,
    )
    proxy_canonical = canonicalize_stats(
        proxy_result.get("stats"),
        ignore_player_order=ignore_player_order,
    )
    matched = (
        bool(direct_result.get("ok"))
        and bool(proxy_result.get("ok"))
        and direct_canonical == proxy_canonical
    )

    return {
        "video": str(video_path),
        "context_image": str(context_image_path) if context_image_path else None,
        "matched": matched,
        "direct": direct_result,
        "proxy": proxy_result,
    }


def write_report(report_path: Path, results: List[Dict[str, object]]):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "all_matched": all(result["matched"] for result in results),
                "results": results,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Compare direct Gemini SDK output with Cloudflare Worker proxy output."
    )
    parser.add_argument(
        "--videos-dir",
        default="testvideos",
        help="Directory containing result-screen videos to compare (default: testvideos)",
    )
    parser.add_argument(
        "--report",
        default="testvideos/gemini_backend_comparison.json",
        help="JSON report path (default: testvideos/gemini_backend_comparison.json)",
    )
    parser.add_argument(
        "--slowdown",
        type=int,
        default=10,
        help="Video slowdown factor passed to both backends (default: 10)",
    )
    parser.add_argument(
        "--model",
        default=get_gemini_model() or DEFAULT_GEMINI_MODEL,
        help=(
            "Gemini model for the direct SDK baseline. The Worker uses its own "
            f"GEMINI_MODEL setting; set it to the same value for exact comparison "
            f"(default: {DEFAULT_GEMINI_MODEL})"
        ),
    )
    parser.add_argument(
        "--ignore-player-order",
        action="store_true",
        help="Treat outputs as matching when the same players are returned in a different order",
    )
    args = parser.parse_args()

    videos_dir = Path(args.videos_dir)
    videos = discover_videos(videos_dir)
    if not videos:
        print(f"No test videos found in {videos_dir}. Add .mp4/.mov/etc files and rerun.")
        return 2

    try:
        direct_client = create_direct_gemini_client()
        proxy_client = create_proxy_gemini_client()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"Direct baseline model: {args.model}")
    print("Worker model: controlled by the Worker's GEMINI_MODEL setting")

    results = []
    for index, video_path in enumerate(videos, start=1):
        print(f"\n[{index}/{len(videos)}] {video_path}")
        result = compare_video(
            video_path=video_path,
            direct_client=direct_client,
            proxy_client=proxy_client,
            slowdown_factor=args.slowdown,
            model=args.model,
            ignore_player_order=args.ignore_player_order,
        )
        results.append(result)
        print("  result:", "MATCH" if result["matched"] else "DIFFERENT")

    report_path = Path(args.report)
    write_report(report_path, results)
    print(f"\nWrote comparison report: {report_path}")

    if all(result["matched"] for result in results):
        print("All direct and proxy outputs matched.")
        return 0

    print("One or more direct/proxy outputs differed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
