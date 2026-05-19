#!/usr/bin/env python3
"""
Extract a Smash result screen and print the union of local OCR text seen in it.

This script does not call Gemini. It is meant to show whether the local OCR blob
is useful enough to include as supporting context for the API.

Usage:
    python ocr_result_screen_union.py path/to/full_match.mp4
    python ocr_result_screen_union.py path/to/result_screen.mp4 --already-result-screen
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from gemini_match_analyzer import (
    DEFAULT_LOCAL_OCR_MAX_FRAMES,
    DEFAULT_MAX_RESULT_SCREEN_SECONDS,
    build_local_ocr_union,
    trim_video_to_max_seconds,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract a result screen from a Smash video and print the local OCR union.",
    )
    parser.add_argument("video_path", help="Full match video path, or a result-screen video with --already-result-screen.")
    parser.add_argument(
        "--already-result-screen",
        action="store_true",
        help="Skip result-screen extraction and run OCR directly on the provided video.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for extracted/clipped artifacts and the OCR text file. Defaults beside the video.",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=DEFAULT_MAX_RESULT_SCREEN_SECONDS,
        help=f"Only OCR the first N seconds of the result screen. Default: {DEFAULT_MAX_RESULT_SCREEN_SECONDS:g}.",
    )
    parser.add_argument(
        "--max-ocr-frames",
        type=int,
        default=DEFAULT_LOCAL_OCR_MAX_FRAMES,
        help=f"Maximum frames to OCR after the time cap. Default: {DEFAULT_LOCAL_OCR_MAX_FRAMES}.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Print OCR output only; do not write the *_ocr_union.txt file.",
    )
    return parser.parse_args()


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
        force=True,
    )
    logging.getLogger("google.auth.transport.requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    return logging.getLogger("ocr_result_screen_union")


def extract_result_screen_video(video_path: str, output_dir: str, logger: logging.Logger) -> str:
    from batch_process_videos import BatchVideoProcessor

    processor = BatchVideoProcessor(
        directory=os.path.dirname(video_path) or ".",
        dry_run=True,
    )
    processor.result_screens_dir = output_dir
    os.makedirs(processor.result_screens_dir, exist_ok=True)
    processor.logger = logger

    frames, _frame_42_image, fps = processor.extract_result_screen(video_path)
    if frames is None or fps is None:
        raise RuntimeError(f"Could not extract a result screen from {video_path}")

    result_video_path = processor.create_result_video(frames, fps, os.path.basename(video_path))
    if not result_video_path:
        raise RuntimeError(f"Could not write result-screen video for {video_path}")

    return result_video_path


def format_ocr_union(
    *,
    source_video_path: str,
    result_video_path: str,
    analysis_video_path: str,
    ocr_union: dict,
) -> str:
    processed_frames = ocr_union["processed_frames"]
    total_frames = ocr_union["total_frames"]
    line_counts = ocr_union["line_counts"]
    line_examples = ocr_union["line_examples"]
    token_counts = ocr_union["token_counts"]

    lines = [
        f"Source video: {source_video_path}",
        f"Result screen video: {result_video_path}",
        f"OCR video used: {analysis_video_path}",
        f"Processed frames: {processed_frames}/{total_frames}",
        f"Unique OCR lines: {len(line_counts)}",
        f"Unique OCR tokens: {len(token_counts)}",
        "",
        "OCR line union:",
    ]

    for canonical_line, count in line_counts.most_common():
        lines.append(f"- {line_examples.get(canonical_line, canonical_line)!r} (seen {count} frame(s))")

    lines.extend(["", "OCR token union:"])
    for token, count in token_counts.most_common():
        lines.append(f"- {token} (seen {count} frame(s))")

    return "\n".join(lines)


def default_output_dir(video_path: str, already_result_screen: bool) -> str:
    video_dir = os.path.dirname(video_path) or "."
    if already_result_screen:
        return video_dir
    return os.path.join(video_dir, "result_screens")


def main() -> int:
    load_dotenv()
    args = parse_args()
    logger = setup_logging()

    video_path = os.path.abspath(args.video_path)
    if not os.path.exists(video_path):
        logger.error("Video file not found: %s", video_path)
        return 1

    os.environ["LOCAL_OCR_HINTS"] = "true"
    os.environ["LOCAL_OCR_MAX_FRAMES"] = str(max(1, args.max_ocr_frames))

    output_dir = os.path.abspath(args.output_dir or default_output_dir(video_path, args.already_result_screen))
    os.makedirs(output_dir, exist_ok=True)

    if args.already_result_screen:
        result_video_path = video_path
    else:
        result_video_path = extract_result_screen_video(video_path, output_dir, logger)

    base_name = os.path.splitext(os.path.basename(result_video_path))[0]
    clipped_video_path = os.path.join(output_dir, f"{base_name}_first_{int(args.max_seconds)}s.mp4")
    analysis_video_path = trim_video_to_max_seconds(
        result_video_path,
        clipped_video_path,
        args.max_seconds,
        logger,
    )

    ocr_union = build_local_ocr_union(analysis_video_path, logger)
    if not ocr_union:
        logger.error("No OCR text was found. Check that Tesseract is installed and the video is readable.")
        return 1

    output_text = format_ocr_union(
        source_video_path=video_path,
        result_video_path=result_video_path,
        analysis_video_path=analysis_video_path,
        ocr_union=ocr_union,
    )
    print(output_text)

    if not args.no_save:
        output_text_path = os.path.join(output_dir, f"{base_name}_ocr_union.txt")
        with open(output_text_path, "w", encoding="utf-8") as output_file:
            output_file.write(output_text)
            output_file.write("\n")
        logger.info("Saved OCR union text: %s", output_text_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
