#!/usr/bin/env python3
"""
Batch Process Saved Match Videos

This script processes full match video files, extracts the result screen portion,
and sends it to Gemini for stats extraction. Sets match created_at timestamp
based on the file's creation date.

Usage:
    python batch_process_videos.py /path/to/videos/directory
    python batch_process_videos.py /path/to/videos/directory --slowdown 10
    python batch_process_videos.py /path/to/videos/directory --dry-run
"""

import argparse
import cv2
import numpy as np
import os
import sys
import time
import logging
from datetime import datetime
from typing import List, Optional
import platform
import re

from gemini_match_analyzer import (
    DEFAULT_GEMINI_MODEL,
    PlayerStats,
    analyze_match_results_video,
    create_gemini_client,
    get_gemini_model,
)
from supabase import create_client, Client
from dotenv import load_dotenv

from elo_utils import (
    calculate_elo_update_for_streaming,
    persist_match_participant_elo_diffs,
    update_character_rankings_for_streaming,
    update_inactivity_status,
    update_team_rankings_for_streaming,
)

# Load environment variables
load_dotenv()

# Initialize Gemini client
try:
    gemini_client = create_gemini_client()
    gemini_model = get_gemini_model()
except Exception as e:
    print(f"Warning: Failed to initialize Gemini client: {e}")
    gemini_client = None
    gemini_model = DEFAULT_GEMINI_MODEL

# Initialize Supabase client
try:
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not found in environment variables")

    supabase_client: Client = create_client(supabase_url, supabase_key)
except Exception as e:
    print(f"Warning: Failed to initialize Supabase client: {e}")
    supabase_client = None


def get_file_creation_time(filepath: str) -> datetime:
    """Get the creation time of a file, cross-platform."""
    if platform.system() == 'Windows':
        timestamp = os.path.getctime(filepath)
    else:
        stat = os.stat(filepath)
        if hasattr(stat, 'st_birthtime'):
            timestamp = stat.st_birthtime
        else:
            timestamp = stat.st_mtime

    return datetime.fromtimestamp(timestamp)


def get_video_files(directory: str) -> List[str]:
    """Get all video files in a directory, sorted by filename."""
    video_extensions = ('.mp4', '.avi', '.mov', '.mkv', '.webm')

    files = []
    for filename in os.listdir(directory):
        if filename.lower().endswith(video_extensions):
            files.append(os.path.join(directory, filename))

    files.sort(key=lambda x: os.path.basename(x))
    return files


class BatchVideoProcessor:
    def __init__(self, directory: str, slowdown_factor: int = 10, dry_run: bool = False):
        self.directory = directory
        self.slowdown_factor = slowdown_factor
        self.dry_run = dry_run
        self.processed_count = 0
        self.skipped_count = 0
        self.failed_count = 0

        # Detection parameters (from capture_card_processor.py)
        self.game_end_confidence_threshold = 0.7
        self.game_region_top = 0.27
        self.game_region_bottom = 0.54
        self.game_region_left = 0.2
        self.game_region_right = 0.8

        # Result screens output directory
        self.result_screens_dir = os.path.join(directory, "result_screens")
        if not os.path.exists(self.result_screens_dir):
            os.makedirs(self.result_screens_dir)

        self.setup_logging()

    def setup_logging(self):
        """Setup logging to file and console"""
        log_filename = "batch_processor.log"
        log_filepath = os.path.join(self.directory, log_filename)

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filepath, mode='w'),
                logging.StreamHandler()
            ],
            force=True
        )

        logging.getLogger('google.auth.transport.requests').setLevel(logging.WARNING)
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)

        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Batch Video Processor started")
        self.logger.info(f"Processing directory: {self.directory}")
        self.logger.info(f"Slowdown factor: {self.slowdown_factor}")
        self.logger.info(f"Dry run: {self.dry_run}")

    def detect_game_end(self, frame) -> tuple:
        """
        Detect game end by looking for 'GAME!' text or victory screen elements
        (Same logic as capture_card_processor.py)
        """
        try:
            h, w = frame.shape[:2]
            game_region = frame[int(h*self.game_region_top):int(h*self.game_region_bottom),
                               int(w*self.game_region_left):int(w*self.game_region_right)]

            gray_game = cv2.cvtColor(game_region, cv2.COLOR_BGR2GRAY)
            bright_mask = gray_game > 200
            bright_ratio = np.sum(bright_mask) / (bright_mask.shape[0] * bright_mask.shape[1])

            confidence = bright_ratio
            return confidence, confidence >= self.game_end_confidence_threshold
        except Exception as e:
            self.logger.error(f"Error in detect_game_end: {e}")
            return 0.0, False

    def extract_result_screen(self, video_path: str) -> tuple:
        """
        Extract the result screen portion from a full match video.
        Returns (result_screen_frames, frame_42_image, fps) or (None, None, None) on failure.
        """
        self.logger.info(f"Extracting result screen from: {video_path}")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self.logger.error(f"Failed to open video: {video_path}")
            return None, None, None

        fps = int(cap.get(cv2.CAP_PROP_FPS))
        if fps <= 0:
            fps = 30

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        self.logger.info(f"Video: {width}x{height} @ {fps}fps, {total_frames} frames")

        # Store frames and their game end confidence scores
        frames = []
        scores = []
        frame_42_image = None
        frame_count = 0
        frame_skip_interval = 2  # Store every 2nd frame
        max_frames = 3600  # ~1 minute at 60fps

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_count += 1

            # Capture frame 42 for player identification
            if frame_count == 42:
                frame_42_image = frame.copy()
                self.logger.info("Captured frame 42 for player identification")

            # Get game end confidence
            confidence, _ = self.detect_game_end(frame)

            # Store every nth frame
            if frame_count % frame_skip_interval == 0:
                frames.append(frame.copy())
                scores.append(confidence)

                # Limit memory usage
                if len(frames) > max_frames:
                    chunk_size = min(50, len(frames) // 4)
                    frames = frames[chunk_size:]
                    scores = scores[chunk_size:]

            # Progress update every 1000 frames
            if frame_count % 1000 == 0:
                self.logger.info(f"Processed {frame_count}/{total_frames} frames...")

        cap.release()

        if not frames or not scores:
            self.logger.warning("No frames captured from video")
            return None, None, None

        # Find the last frame with highest game end confidence above threshold
        best_frame_index = -1
        best_confidence = 0.0

        for i in range(len(scores) - 1, -1, -1):
            confidence = scores[i]
            if confidence >= self.game_end_confidence_threshold and confidence > best_confidence:
                best_confidence = confidence
                best_frame_index = i
                break

        if best_frame_index == -1:
            self.logger.warning("No frame found with game end confidence above threshold")
            return None, None, None

        # Extract frames from the best frame to the end
        result_frames = frames[best_frame_index:]

        if len(result_frames) < 15:  # Less than ~0.5 seconds
            self.logger.warning(f"Result screen sequence too short ({len(result_frames)} frames)")
            return None, None, None

        self.logger.info(f"Extracted {len(result_frames)} result screen frames (confidence: {best_confidence:.3f})")

        return result_frames, frame_42_image, fps

    def create_result_video(self, frames: List, fps: int, source_filename: str) -> Optional[str]:
        """Create a result screen video file and save to result_screens directory."""
        if not frames:
            return None

        # Create filename based on source video name
        base_name = os.path.splitext(source_filename)[0]
        result_filename = f"{base_name}_result_screen.mp4"
        result_path = os.path.join(self.result_screens_dir, result_filename)

        height, width = frames[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(result_path, fourcc, fps, (width, height))

        if not out.isOpened():
            self.logger.error("Failed to create video writer")
            return None

        for frame in frames:
            out.write(frame)

        out.release()
        self.logger.info(f"Saved result screen video: {result_filename}")
        return result_path

    def get_match_stats(self, result_video_path: str, frame_42_path: Optional[str] = None) -> Optional[List[PlayerStats]]:
        """Extract player stats from result screen video using Gemini API"""
        if not gemini_client:
            self.logger.error("Gemini client not available")
            return None

        try:
            self.logger.info("Processing result screen video for Gemini...")
            self.logger.info(f"Using Gemini model: {gemini_model}")

            cap = cv2.VideoCapture(result_video_path)
            fps = int(cap.get(cv2.CAP_PROP_FPS))
            if fps <= 0:
                fps = 30
            cap.release()

            player_stats = analyze_match_results_video(
                gemini_client,
                result_video_path,
                context_image_path=frame_42_path,
                slowdown_factor=self.slowdown_factor,
                output_fps=fps,
                model=gemini_model,
                logger=self.logger,
            )

            if not player_stats:
                return None

            for i, stat in enumerate(player_stats):
                self.logger.info(f"Player {i+1}: {stat.player_name} ({stat.smash_character}) - KOs: {stat.total_kos}, Falls: {stat.total_falls}, SDs: {stat.total_sds}, Won: {stat.has_won}")

            return player_stats

        except Exception as e:
            self.logger.error(f"Error extracting match stats: {e}")
            return None

    def get_player(self, player_name: str) -> Optional[dict]:
        """Get or create a player in the database"""
        if not supabase_client:
            return None

        try:
            response = (
                supabase_client.table("players")
                .upsert({"display_name": player_name}, on_conflict="display_name")
                .execute()
            )
            return response.data[0]
        except Exception as e:
            self.logger.error(f"Error getting/creating player {player_name}: {e}")
            return None

    def update_player_elo(self, player_id: str, elo: int):
        """Update a player's ELO in the database"""
        if not supabase_client:
            return

        try:
            supabase_client.table("players").update({"elo": elo}).eq("id", player_id).execute()
        except Exception as e:
            self.logger.error(f"Error updating player ELO: {e}")

    def create_match(self, created_at: datetime) -> Optional[int]:
        """Create a new match in the database with specified creation time"""
        if not supabase_client:
            return None

        try:
            created_at_iso = created_at.isoformat()
            response = (
                supabase_client.table("matches")
                .insert({"created_at": created_at_iso})
                .execute()
            )
            return response.data[0]['id']
        except Exception as e:
            self.logger.error(f"Error creating match: {e}")
            return None

    def save_match_stats(self, stats: List[PlayerStats], created_at: datetime) -> bool:
        """Save match stats to the database with specified creation time"""
        if not supabase_client:
            self.logger.error("Supabase client not available")
            return False

        try:
            # Skip no contest matches
            match_is_no_contest = all(not stat.has_won for stat in stats)
            if match_is_no_contest:
                self.logger.warning("Match is a no contest, skipping database save")
                return False

            # Skip matches with CPU players
            match_has_cpu = any(stat.is_cpu for stat in stats)
            if match_has_cpu:
                self.logger.warning("Match has CPU players, skipping database save")
                return False

            # Skip matches with unknown players
            for stat in stats:
                if re.match(r"^Player \d+$", stat.player_name) or re.match(r"^P\d+$", stat.player_name) or re.match(r"^P \d+$", stat.player_name):
                    self.logger.warning("Match has unknown players (Player 1,2,3,etc.), skipping database save")
                    return False

            # Skip online matches
            if stats[0].is_online_match:
                self.logger.warning("Match is online, skipping database save")
                return False

            # Create match with the file's creation date
            match_id = self.create_match(created_at)
            if match_id is None:
                return False

            players = []
            winners = []

            self.logger.info(f"Saving match stats to database (Match ID: {match_id}, Created: {created_at})")

            for stat in stats:
                player = self.get_player(stat.player_name)
                if player is None:
                    continue

                # Save match participant with same created_at as match
                response = supabase_client.table("match_participants").insert({
                    "player": player['id'],
                    "smash_character": stat.smash_character.upper(),
                    "elo_diff": None,
                    "is_cpu": stat.is_cpu,
                    "total_kos": stat.total_kos,
                    "total_falls": stat.total_falls,
                    "total_sds": stat.total_sds,
                    "has_won": stat.has_won,
                    "match_id": match_id,
                    "created_at": created_at.isoformat(),
                }).execute()

                players.append({
                    "id": player['id'],
                    "participant_id": (
                        response.data[0]['id']
                        if response.data and len(response.data) > 0
                        else None
                    ),
                    "elo": player['elo'],
                    "name": player['display_name'],
                    "character": stat.smash_character,
                    "has_won": stat.has_won,
                    "kos": stat.total_kos,
                    "falls": stat.total_falls,
                    "sds": stat.total_sds
                })

                if stat.has_won:
                    winners.append(player['display_name'])

            # Print match results
            self.logger.info("=" * 60)
            self.logger.info("MATCH RESULTS")
            self.logger.info("=" * 60)

            if winners:
                self.logger.info(f"Winner(s): {', '.join(winners)}")
            else:
                self.logger.info("No Contest")

            self.logger.info("Player Stats:")
            for player in players:
                status = "WINNER" if player['has_won'] else ""
                self.logger.info(f"  {player['name']} ({player['character']}) - KOs: {player['kos']}, Falls: {player['falls']}, SDs: {player['sds']} {status}")

            # Update ELO ratings for 1v1 matches
            if len(stats) == 2:
                self.logger.info("1v1 Match detected - Updating ELO ratings:")

                old_elo_1 = players[0]['elo']
                old_elo_2 = players[1]['elo']

                winner_index = 1 if players[0]['has_won'] else 2
                winner = 'A' if winner_index == 1 else 'B'

                new_elo_1, new_elo_2, elo_metadata = calculate_elo_update_for_streaming(
                    old_elo_1, old_elo_2, winner,
                    players[0]['id'], players[1]['id'],
                    supabase_client,
                    return_metadata=True,
                )

                elo_change_1 = new_elo_1 - old_elo_1
                elo_change_2 = new_elo_2 - old_elo_2

                if not elo_metadata.get("triggered_full_recompute"):
                    self.update_player_elo(players[0]['id'], new_elo_1)
                    self.update_player_elo(players[1]['id'], new_elo_2)

                    if elo_change_1 != 0 or elo_change_2 != 0:
                        persist_match_participant_elo_diffs(
                            [
                                {
                                    "id": players[0].get("participant_id"),
                                    "match_id": match_id,
                                    "player": players[0]['id'],
                                    "elo_diff": elo_change_1,
                                },
                                {
                                    "id": players[1].get("participant_id"),
                                    "match_id": match_id,
                                    "player": players[1]['id'],
                                    "elo_diff": elo_change_2,
                                },
                            ],
                            supabase_client,
                        )
                else:
                    self.logger.info(
                        "  Full recompute handled player ELO and participant elo_diff updates."
                    )

                if not elo_metadata.get("triggered_full_recompute"):
                    new_character_elos = update_character_rankings_for_streaming(
                        players[0]['id'],
                        players[1]['id'],
                        players[0]['character'],
                        players[1]['character'],
                        winner,
                        {
                            "kos": players[0]['kos'],
                            "falls": players[0]['falls'],
                            "sds": players[0]['sds'],
                        },
                        {
                            "kos": players[1]['kos'],
                            "falls": players[1]['falls'],
                            "sds": players[1]['sds'],
                        },
                        supabase_client,
                        match_created_at=created_at,
                    )

                    if new_character_elos:
                        char_elo_1, char_elo_2 = new_character_elos
                        self.logger.info(
                            f"  Character ELOs: {players[0]['character']} -> {char_elo_1}, "
                            f"{players[1]['character']} -> {char_elo_2}"
                        )

                self.logger.info(f"  {players[0]['name']}: {old_elo_1} -> {new_elo_1} ({elo_change_1:+d})")
                self.logger.info(f"  {players[1]['name']}: {old_elo_2} -> {new_elo_2} ({elo_change_2:+d})")

            if len(players) == 4:
                self.logger.info("2v2 Match detected - Updating team ELO ratings:")
                team_elo_result = update_team_rankings_for_streaming(
                    players,
                    supabase_client,
                    match_created_at=created_at,
                )

                if team_elo_result:
                    winning_names = [
                        player["name"] for player in players if player["has_won"]
                    ]
                    losing_names = [
                        player["name"] for player in players if not player["has_won"]
                    ]
                    winning_team = team_elo_result["winning_team"]
                    losing_team = team_elo_result["losing_team"]
                    self.logger.info(
                        "  "
                        f"{' + '.join(winning_names)}: "
                        f"{team_elo_result['old_winning_elo']} -> {winning_team['elo']}"
                    )
                    self.logger.info(
                        "  "
                        f"{' + '.join(losing_names)}: "
                        f"{team_elo_result['old_losing_elo']} -> {losing_team['elo']}"
                    )
                else:
                    self.logger.info("  Team ELO unchanged; match was not eligible.")

            # Update inactivity status for all players
            self.logger.info("Updating inactivity status...")
            update_inactivity_status(supabase_client)

            self.logger.info("=" * 60)
            return True

        except Exception as e:
            self.logger.error(f"Error saving match stats: {e}")
            return False

    def process_video(self, video_path: str) -> bool:
        """Process a single full match video file"""
        filename = os.path.basename(video_path)
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"Processing: {filename}")
        self.logger.info(f"{'='*60}")

        # Get file creation time
        created_at = get_file_creation_time(video_path)
        self.logger.info(f"File creation time: {created_at}")

        if self.dry_run:
            self.logger.info(f"[DRY RUN] Would process {filename} with created_at={created_at}")
            return True

        # Extract result screen from the full match video
        result_frames, frame_42_image, fps = self.extract_result_screen(video_path)

        if result_frames is None:
            self.logger.error(f"Failed to extract result screen from {filename}")
            return False

        # Create result screen video (saved to result_screens directory)
        result_video_path = self.create_result_video(result_frames, fps, filename)
        if result_video_path is None:
            self.logger.error(f"Failed to create result video for {filename}")
            return False

        # Save frame 42 image to result_screens directory if available
        frame_42_path = None
        if frame_42_image is not None:
            base_name = os.path.splitext(filename)[0]
            frame_42_path = os.path.join(self.result_screens_dir, f"{base_name}_frame_42.png")
            cv2.imwrite(frame_42_path, frame_42_image)
            self.logger.info(f"Saved frame 42 image: {base_name}_frame_42.png")

        # Extract match stats using Gemini
        match_stats = self.get_match_stats(result_video_path, frame_42_path)

        if not match_stats:
            self.logger.error(f"Failed to extract match stats from {filename}")
            return False

        # Save to database with the file's creation time
        success = self.save_match_stats(match_stats, created_at)

        if success:
            self.logger.info(f"Successfully processed and saved: {filename}")
        else:
            self.logger.warning(f"Match results extracted but not saved to database: {filename}")

        return success

    def process_all(self):
        """Process all videos in the directory"""
        video_files = get_video_files(self.directory)

        if not video_files:
            self.logger.error(f"No video files found in {self.directory}")
            return

        self.logger.info(f"Found {len(video_files)} video files to process")

        for i, video_path in enumerate(video_files, 1):
            self.logger.info(f"\n[{i}/{len(video_files)}] Processing...")

            try:
                success = self.process_video(video_path)
                if success:
                    self.processed_count += 1
                else:
                    self.skipped_count += 1
            except Exception as e:
                self.logger.error(f"Error processing {video_path}: {e}")
                self.failed_count += 1

            # Small delay between API calls to avoid rate limiting
            if not self.dry_run and i < len(video_files):
                time.sleep(2)

        # Print summary
        self.logger.info("\n" + "=" * 60)
        self.logger.info("BATCH PROCESSING COMPLETE")
        self.logger.info("=" * 60)
        self.logger.info(f"Total videos: {len(video_files)}")
        self.logger.info(f"Successfully processed: {self.processed_count}")
        self.logger.info(f"Skipped (no contest/CPU/unknown/online/no result screen): {self.skipped_count}")
        self.logger.info(f"Failed: {self.failed_count}")


def main():
    parser = argparse.ArgumentParser(description='Batch process full Smash Bros match videos')
    parser.add_argument('directory', type=str, help='Path to directory containing video files')
    parser.add_argument('--slowdown', type=int, default=10, help='Video slowdown factor for Gemini (default: 10)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be processed without actually processing')

    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: Directory not found: {args.directory}")
        sys.exit(1)

    processor = BatchVideoProcessor(args.directory, args.slowdown, args.dry_run)
    processor.process_all()


if __name__ == "__main__":
    main()
