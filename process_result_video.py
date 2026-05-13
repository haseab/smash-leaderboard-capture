#!/usr/bin/env python3
"""
Standalone Result Video Processor for Smash Bros

This script processes result screen videos through Gemini API and saves match stats to the database.
Can be used to reprocess videos or process manually captured result screens.

Usage:
    python process_result_video.py result_screen_video.mp4
    python process_result_video.py result_screen_video.mp4 --slowdown 5
    python process_result_video.py result_screen_video.mp4 --force-save
"""

import argparse
import datetime
import os
import sys
import logging
from typing import List, Optional, Dict, Tuple
from gemini_match_analyzer import (
    DEFAULT_GEMINI_MODEL,
    PlayerStats,
    analyze_match_results_video,
    create_gemini_client,
    get_gemini_model,
)
from supabase import create_client, Client
from dotenv import load_dotenv
import re
import pandas as pd
import pytz
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

class ResultVideoProcessor:
    def __init__(self, video_path: str, slowdown_factor: int = 5, force_save: bool = False):
        self.video_path = video_path
        self.slowdown_factor = slowdown_factor
        self.force_save = force_save
        self.setup_logging()
    
    def setup_logging(self):
        """Setup logging to file and console"""
        # Use a fixed log filename that gets overwritten each time
        log_filename = "result_processor.log"
        
        # Create logs directory if it doesn't exist
        log_dir = os.path.dirname(self.video_path)
        log_filepath = os.path.join(log_dir, log_filename)
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filepath, mode='w'),  # 'w' mode overwrites the file
                logging.StreamHandler()
            ],
            force=True  # Force reconfiguration if already configured
        )
        
        # Suppress verbose HTTP logging from Google API client
        logging.getLogger('google.auth.transport.requests').setLevel(logging.WARNING)
        logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
        
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Result Video Processor started - Log file: {log_filename}")
        self.logger.info(f"Processing video: {self.video_path}")
        self.logger.info(f"Slowdown factor: {self.slowdown_factor}")
        self.logger.info(f"Force save: {self.force_save}")
    
    def get_match_stats(self) -> Optional[List[PlayerStats]]:
        """Extract player stats from result screen video using Gemini API"""
        if not gemini_client:
            self.logger.error("Gemini client not available")
            return None
        
        if not os.path.exists(self.video_path):
            self.logger.error(f"Video file not found: {self.video_path}")
            return None
        
        try:
            self.logger.info(f"Processing result screen video: {self.video_path}")

            self.logger.info(f"Using Gemini model: {gemini_model}")
            player_stats = analyze_match_results_video(
                gemini_client,
                self.video_path,
                slowdown_factor=self.slowdown_factor,
                model=gemini_model,
                logger=self.logger,
            )

            if not player_stats:
                return None

            # Log the extracted stats
            for i, stat in enumerate(player_stats):
                self.logger.info(f"Player {i+1}: {stat.player_name} ({stat.smash_character}) - KOs: {stat.total_kos}, Falls: {stat.total_falls}, SDs: {stat.total_sds}, Won: {stat.has_won}")

            return player_stats
            
        except Exception as e:
            self.logger.error(f"Error extracting match stats: {e}")
            return None
    
    def update_elo(self, rating_a: float, rating_b: float, winner: str, k: int = 32) -> tuple[int, int]:
        """Update ELO ratings after a match"""
        # Expected scores (logistic curve)
        expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
        expected_b = 1 - expected_a

        # Actual scores
        if winner.upper() == 'A':
            score_a, score_b = 1.0, 0.0
        elif winner.upper() == 'B':
            score_a, score_b = 0.0, 1.0
        elif winner.lower() in ('draw', 'tie', 'd'):
            score_a = score_b = 0.5
        else:
            raise ValueError("winner must be 'A', 'B', or 'draw'")

        # Rating updates
        new_rating_a = rating_a + k * (score_a - expected_a)
        new_rating_b = rating_b + k * (score_b - expected_b)

        # Return as integers
        return round(new_rating_a), round(new_rating_b)
    
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
    
    def create_match(self) -> Optional[int]:
        """Create a new match in the database"""
        if not supabase_client:
            return None
        
        try:
            response = (
                supabase_client.table("matches")
                .insert({})
                .execute()
            )
            return response.data[0]['id']
        except Exception as e:
            self.logger.error(f"Error creating match: {e}")
            return None
    
    def save_match_stats(self, stats: List[PlayerStats]) -> bool:
        """Save match stats to the database"""
        if not supabase_client:
            self.logger.error("Supabase client not available")
            return False
        
        try:
            # Check if match should be skipped
            if not self.force_save:
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
                match_has_unknown_players = False
                for stat in stats:
                    if re.match(r"^Player \d+$", stat.player_name) or re.match(r"^P\d+$", stat.player_name) or re.match(r"^P \d+$", stat.player_name):
                        match_has_unknown_players = True
                        break
                
                if match_has_unknown_players:
                    self.logger.warning("Match has unknown players (Player 1,2,3,etc.), skipping database save")
                    return False
                
                # Skip online matches
                if stats[0].is_online_match:
                    self.logger.warning("Match is online, skipping database save")
                    return False
            
            # Create match
            match_id = self.create_match()
            if match_id is None:
                return False
            
            players = []
            winners = []
            
            self.logger.info(f"Saving match stats to database (Match ID: {match_id})")
            
            for stat in stats:
                player = self.get_player(stat.player_name)
                if player is None:
                    continue
                
                # Save match participant
                response = (
                    supabase_client.table("match_participants")
                    .insert({
                        "player": player['id'], 
                        "smash_character": stat.smash_character.upper(),
                        "elo_diff": None,
                        "is_cpu": stat.is_cpu,
                        "total_kos": stat.total_kos,
                        "total_falls": stat.total_falls,
                        "total_sds": stat.total_sds,
                        "has_won": stat.has_won,
                        "match_id": match_id,
                    })
                    .execute()
                )
                
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
                self.logger.info(f"🏆 Winner(s): {', '.join(winners)}")
            else:
                self.logger.info("🤝 No Contest")
            
            self.logger.info("Player Stats:")
            for player in players:
                status = "🏆 WINNER" if player['has_won'] else ""
                self.logger.info(f"  {player['name']} ({player['character']}) - KOs: {player['kos']}, Falls: {player['falls']}, SDs: {player['sds']} {status}")
            
            # Update ELO ratings for 1v1 matches
            if len(stats) == 2:
                self.logger.info("1v1 Match detected - Updating ELO ratings:")
                
                old_elo_1 = players[0]['elo']
                old_elo_2 = players[1]['elo']
                
                winner_index = 1 if players[0]['has_won'] else 2
                winner = 'A' if winner_index == 1 else 'B'
                
                # Use shared ELO calculation
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
                        match_created_at=datetime.datetime.now(datetime.timezone.utc),
                    )

                    if new_character_elos:
                        char_elo_1, char_elo_2 = new_character_elos
                        self.logger.info(
                            f"  Character ELOs: {players[0]['character']} -> {char_elo_1}, "
                            f"{players[1]['character']} -> {char_elo_2}"
                        )
                
                self.logger.info(f"  {players[0]['name']}: {old_elo_1} → {new_elo_1} ({elo_change_1:+d})")
                self.logger.info(f"  {players[1]['name']}: {old_elo_2} → {new_elo_2} ({elo_change_2:+d})")

            if len(players) == 4:
                self.logger.info("2v2 Match detected - Updating team ELO ratings:")
                team_elo_result = update_team_rankings_for_streaming(
                    players,
                    supabase_client,
                    match_created_at=datetime.datetime.now(datetime.timezone.utc),
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
                        f"{team_elo_result['old_winning_elo']} → {winning_team['elo']}"
                    )
                    self.logger.info(
                        "  "
                        f"{' + '.join(losing_names)}: "
                        f"{team_elo_result['old_losing_elo']} → {losing_team['elo']}"
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
    
    def process(self):
        """Main processing function"""
        self.logger.info("Starting result video processing...")
        
        # Extract match stats
        match_stats = self.get_match_stats()
        
        if not match_stats:
            self.logger.error("Failed to extract match stats")
            return False
        
        # Save to database
        success = self.save_match_stats(match_stats)
        
        if success:
            self.logger.info("Successfully processed and saved match results")
        else:
            self.logger.warning("Match results extracted but not saved to database")
        
        return success

def main():
    parser = argparse.ArgumentParser(description='Process Smash Bros result screen videos')
    parser.add_argument('video_path', type=str, help='Path to the result screen video file')
    parser.add_argument('--slowdown', type=int, default=5, help='Video slowdown factor (default: 5)')
    parser.add_argument('--force-save', action='store_true', help='Force save even if match has CPU/unknown players/is online')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.video_path):
        print(f"Error: Video file not found: {args.video_path}")
        sys.exit(1)
    
    # Create processor and run
    processor = ResultVideoProcessor(args.video_path, args.slowdown, args.force_save)
    success = processor.process()
    
    if success:
        print("✅ Processing completed successfully!")
        sys.exit(0)
    else:
        print("❌ Processing failed or match was skipped")
        sys.exit(1)

if __name__ == "__main__":
    main()
