#!/usr/bin/env python3
"""
Bulk YouTube Upload Script for Smash Bros Leaderboard
Uploads existing match videos from matches/ and matches/forlater/ directories
"""

import os
import sys
import re
import argparse
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client
from tqdm import tqdm
import pytz

from youtube_uploader import upload_video, get_uploader

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BulkUploader:
    """Handles bulk uploading of existing match videos"""
    
    def __init__(self, supabase_client: Client, dry_run: bool = False):
        self.supabase = supabase_client
        self.dry_run = dry_run
        self.uploader = get_uploader()
        
        # Statistics
        self.stats = {
            'total_files': 0,
            'already_uploaded': 0,
            'successful': 0,
            'failed': 0,
            'skipped': 0
        }
    
    def parse_filename(self, filename: str) -> Optional[Dict]:
        """
        Parse match filename to extract match_id and timestamp
        
        Formats:
        - {match_id}-{timestamp}.mp4  (e.g., 42-20240115_143052.mp4)
        - {timestamp}.mp4              (e.g., 20240115_143052.mp4)
        
        Returns dict with 'match_id' (int or None) and 'timestamp' (datetime)
        """
        # Remove .mp4 extension
        base = filename.replace('.mp4', '')
        
        # Pattern 1: match_id-timestamp
        match = re.match(r'^(\d+)-(\d{8}_\d{6})$', base)
        if match:
            match_id = int(match.group(1))
            timestamp_str = match.group(2)
            timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            return {'match_id': match_id, 'timestamp': timestamp}
        
        # Pattern 2: timestamp only
        match = re.match(r'^(\d{8}_\d{6})$', base)
        if match:
            timestamp_str = match.group(1)
            timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            return {'match_id': None, 'timestamp': timestamp}
        
        logger.warning(f"Could not parse filename: {filename}")
        return None
    
    def find_match_by_timestamp(self, timestamp: datetime, tolerance_seconds: int = 5) -> Optional[Dict]:
        """
        Find database match by timestamp (within tolerance window)
        
        Args:
            timestamp: Timestamp from filename
            tolerance_seconds: Match if within ±N seconds (default: 5)
        
        Returns:
            Match record dict or None
        """
        try:
            # Convert to UTC for database comparison
            timestamp_utc = timestamp.replace(tzinfo=pytz.UTC)
            
            # Query range: timestamp ± tolerance
            start_time = timestamp_utc - timedelta(seconds=tolerance_seconds)
            end_time = timestamp_utc + timedelta(seconds=tolerance_seconds)
            
            response = (
                self.supabase.table("matches")
                .select("*, match_participants(player, smash_character, is_cpu, total_kos, total_falls, total_sds, has_won)")
                .gte("created_at", start_time.isoformat())
                .lte("created_at", end_time.isoformat())
                .execute()
            )
            
            if response.data and len(response.data) > 0:
                # Return closest match
                matches = response.data
                if len(matches) == 1:
                    return matches[0]
                
                # Multiple matches found - pick closest
                logger.warning(f"Found {len(matches)} matches near {timestamp}, using closest")
                closest = min(matches, key=lambda m: abs(
                    (datetime.fromisoformat(m['created_at'].replace('Z', '+00:00')) - timestamp_utc).total_seconds()
                ))
                return closest
            
            logger.debug(f"No database match found for timestamp {timestamp}")
            return None
            
        except Exception as e:
            logger.error(f"Error finding match by timestamp: {e}")
            return None
    
    def get_match_metadata(self, match_record: Dict) -> Dict:
        """Build metadata dict from database match record"""
        try:
            players = []
            participants = match_record.get('match_participants', [])
            
            for p in participants:
                # Get player name from database
                player_id = p.get('player')
                player_name = "Unknown"
                
                if player_id:
                    try:
                        player_response = self.supabase.table("players").select("display_name").eq("id", player_id).execute()
                        if player_response.data and len(player_response.data) > 0:
                            player_name = player_response.data[0].get('display_name', 'Unknown')
                    except Exception as e:
                        logger.warning(f"Failed to fetch player name: {e}")
                
                players.append({
                    'name': player_name,
                    'character': p.get('smash_character', 'Unknown'),
                    'kos': p.get('total_kos', 0),
                    'falls': p.get('total_falls', 0),
                    'sds': p.get('total_sds', 0),
                    'won': p.get('has_won', False)
                })
            
            # Get timestamp
            created_at = match_record.get('created_at')
            if created_at:
                timestamp = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            else:
                timestamp = datetime.now()
            
            return {
                'players': players,
                'timestamp': timestamp
            }
        except Exception as e:
            logger.error(f"Error building metadata: {e}")
            return {'players': [], 'timestamp': datetime.now()}
    
    def build_legacy_metadata(self, timestamp: datetime) -> Dict:
        """Build metadata for legacy files without database records"""
        return {
            'players': [
                {'name': 'Unknown', 'character': 'Unknown', 'kos': 0, 'falls': 0, 'sds': 0, 'won': False}
            ],
            'timestamp': timestamp
        }
    
    def is_already_uploaded(self, match_id: Optional[int]) -> bool:
        """Check if match already has youtube_url in database"""
        if match_id is None:
            return False
        
        try:
            response = self.supabase.table("matches").select("youtube_url").eq("id", match_id).execute()
            if response.data and len(response.data) > 0:
                youtube_url = response.data[0].get('youtube_url')
                return youtube_url is not None and youtube_url != ''
        except Exception as e:
            logger.warning(f"Error checking upload status: {e}")
        
        return False
    
    def save_youtube_url(self, match_id: int, youtube_url: str) -> bool:
        """Save YouTube URL to database"""
        try:
            self.supabase.table("matches").update({
                "youtube_url": youtube_url
            }).eq("id", match_id).execute()
            logger.debug(f"Saved YouTube URL for match {match_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save YouTube URL: {e}")
            return False
    
    def process_video(
        self, 
        filepath: str, 
        skip_uploaded: bool = True,
        playlist_name: Optional[str] = "Smash Bros Matches"
    ) -> bool:
        """
        Process and upload a single video
        
        Returns:
            True if uploaded successfully, False otherwise
        """
        filename = os.path.basename(filepath)
        
        # Parse filename
        parsed = self.parse_filename(filename)
        if not parsed:
            logger.warning(f"Skipping unparseable file: {filename}")
            self.stats['skipped'] += 1
            return False
        
        match_id = parsed['match_id']
        timestamp = parsed['timestamp']
        
        # Try to find match in database if no match_id in filename
        match_record = None
        if match_id is None:
            match_record = self.find_match_by_timestamp(timestamp)
            if match_record:
                match_id = match_record['id']
                logger.info(f"Matched {filename} to database record (Match ID: {match_id})")
        else:
            # Fetch match record for metadata
            try:
                response = (
                    self.supabase.table("matches")
                    .select("*, match_participants(player, smash_character, is_cpu, total_kos, total_falls, total_sds, has_won)")
                    .eq("id", match_id)
                    .execute()
                )
                if response.data and len(response.data) > 0:
                    match_record = response.data[0]
            except Exception as e:
                logger.warning(f"Failed to fetch match record: {e}")
        
        # Check if already uploaded
        if skip_uploaded and match_id and self.is_already_uploaded(match_id):
            logger.info(f"Skipping already uploaded: {filename}")
            self.stats['already_uploaded'] += 1
            return False
        
        # Build metadata
        if match_record:
            metadata = self.get_match_metadata(match_record)
        else:
            metadata = self.build_legacy_metadata(timestamp)
            # Use timestamp as match_id for legacy files
            if match_id is None:
                match_id = int(timestamp.timestamp())
        
        # Dry run mode
        if self.dry_run:
            logger.info(f"[DRY RUN] Would upload: {filename} (Match ID: {match_id})")
            return True
        
        # Upload to YouTube
        logger.info(f"Uploading: {filename}")
        youtube_url = upload_video(filepath, match_id, metadata, playlist_name)
        
        if youtube_url:
            logger.info(f"Successfully uploaded: {youtube_url}")
            
            # Save URL to database if we have a real match_id
            if match_record:
                self.save_youtube_url(match_id, youtube_url)
            
            self.stats['successful'] += 1
            return True
        else:
            logger.error(f"Failed to upload: {filename}")
            self.stats['failed'] += 1
            return False
    
    def scan_directory(self, directory: str) -> List[str]:
        """Scan directory for match video files"""
        video_files = []
        
        if not os.path.exists(directory):
            logger.warning(f"Directory not found: {directory}")
            return video_files
        
        for filename in os.listdir(directory):
            # Only process main match files (exclude result_screen files)
            if filename.endswith('.mp4') and 'result_screen' not in filename:
                filepath = os.path.join(directory, filename)
                if os.path.isfile(filepath):
                    video_files.append(filepath)
        
        return sorted(video_files)
    
    def run(
        self, 
        directories: List[str], 
        skip_uploaded: bool = True,
        playlist_name: Optional[str] = "Smash Bros Matches"
    ):
        """
        Run bulk upload process
        
        Args:
            directories: List of directories to scan
            skip_uploaded: Skip videos already uploaded
            playlist_name: YouTube playlist name
        """
        # Collect all video files
        all_files = []
        for directory in directories:
            files = self.scan_directory(directory)
            all_files.extend(files)
            logger.info(f"Found {len(files)} videos in {directory}")
        
        self.stats['total_files'] = len(all_files)
        
        if not all_files:
            logger.info("No video files found to upload")
            return
        
        logger.info(f"Processing {len(all_files)} video files...")
        
        # Process each file with progress bar
        with tqdm(all_files, desc="Uploading videos", unit="video") as pbar:
            for filepath in pbar:
                filename = os.path.basename(filepath)
                pbar.set_description(f"Uploading {filename[:30]}...")
                
                try:
                    self.process_video(filepath, skip_uploaded, playlist_name)
                except Exception as e:
                    logger.error(f"Unexpected error processing {filename}: {e}")
                    self.stats['failed'] += 1
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print upload summary statistics"""
        print("\n" + "="*60)
        print("BULK UPLOAD SUMMARY")
        print("="*60)
        print(f"Total files found:      {self.stats['total_files']}")
        print(f"Already uploaded:       {self.stats['already_uploaded']}")
        print(f"Successfully uploaded:  {self.stats['successful']}")
        print(f"Failed:                 {self.stats['failed']}")
        print(f"Skipped:                {self.stats['skipped']}")
        print("="*60)
        
        if self.stats['failed'] > 0:
            print("\nNote: Failed uploads may be due to quota limits or network issues.")
            print("You can re-run this script to retry failed uploads.")


def main():
    parser = argparse.ArgumentParser(
        description="Bulk upload Smash Bros match videos to YouTube",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview uploads without actually uploading
  python bulk_upload_to_youtube.py --dry-run
  
  # Upload all videos, skip already uploaded
  python bulk_upload_to_youtube.py --skip-uploaded
  
  # Upload only from specific directory
  python bulk_upload_to_youtube.py --directory matches/forlater
  
  # Force re-upload all videos
  python bulk_upload_to_youtube.py --force
        """
    )
    
    parser.add_argument(
        '--directory',
        type=str,
        nargs='+',
        default=['matches', 'matches/forlater'],
        help='Directory or directories to scan (default: matches and matches/forlater)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview what would be uploaded without actually uploading'
    )
    
    parser.add_argument(
        '--skip-uploaded',
        action='store_true',
        default=True,
        help='Skip videos that already have youtube_url in database (default: True)'
    )
    
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force re-upload all videos (overrides --skip-uploaded)'
    )
    
    parser.add_argument(
        '--playlist',
        type=str,
        default='Smash Bros Matches',
        help='YouTube playlist name (default: "Smash Bros Matches")'
    )
    
    parser.add_argument(
        '--no-playlist',
        action='store_true',
        help='Do not add videos to playlist'
    )
    
    args = parser.parse_args()
    
    # Handle --force flag
    skip_uploaded = args.skip_uploaded and not args.force
    
    # Handle playlist
    playlist_name = None if args.no_playlist else args.playlist
    
    # Initialize Supabase
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.error("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not found in environment")
            sys.exit(1)
        
        supabase = create_client(supabase_url, supabase_key)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")
        sys.exit(1)
    
    # Create uploader and run
    uploader = BulkUploader(supabase, dry_run=args.dry_run)
    
    if args.dry_run:
        print("\n*** DRY RUN MODE - No videos will be uploaded ***\n")
    
    uploader.run(args.directory, skip_uploaded, playlist_name)


if __name__ == "__main__":
    main()
