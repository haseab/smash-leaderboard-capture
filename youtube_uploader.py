#!/usr/bin/env python3
"""
YouTube Video Uploader for Smash Bros Leaderboard
Handles OAuth authentication and video uploads to YouTube.
"""

import os
import sys
import pickle
import logging
from typing import Optional, Dict, List
from datetime import datetime

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
import httplib2

# OAuth 2.0 scope for uploading videos
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"
CLIENT_SECRETS_FILE = "client_secrets.json"
CREDENTIALS_FILE = "youtube-upload-credentials.pickle"

# Configure logging
logger = logging.getLogger(__name__)


class YouTubeUploader:
    """Handles YouTube video uploads with OAuth authentication"""
    
    def __init__(self):
        self.youtube = None
        self.daily_upload_count = 0
        self.last_reset_date = datetime.now().date()
        self.default_playlist_id = None
        
    def get_authenticated_service(self) -> Optional[object]:
        """
        Authenticate with YouTube API using OAuth 2.0
        Returns authenticated YouTube service client
        """
        credentials = None
        
        # Load saved credentials if they exist
        if os.path.exists(CREDENTIALS_FILE):
            try:
                with open(CREDENTIALS_FILE, 'rb') as token:
                    credentials = pickle.load(token)
            except Exception as e:
                logger.warning(f"Failed to load saved credentials: {e}")
        
        # If credentials don't exist or are invalid, get new ones
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except Exception as e:
                    logger.error(f"Failed to refresh credentials: {e}")
                    credentials = None
            
            if not credentials:
                if not os.path.exists(CLIENT_SECRETS_FILE):
                    logger.error(f"OAuth credentials file not found: {CLIENT_SECRETS_FILE}")
                    logger.error("Please complete the Google Cloud setup and download client_secrets.json")
                    return None
                
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        CLIENT_SECRETS_FILE, 
                        YOUTUBE_UPLOAD_SCOPE
                    )
                    credentials = flow.run_local_server(port=0)
                except Exception as e:
                    logger.error(f"OAuth authentication failed: {e}")
                    return None
            
            # Save credentials for future use
            try:
                with open(CREDENTIALS_FILE, 'wb') as token:
                    pickle.dump(credentials, token)
            except Exception as e:
                logger.warning(f"Failed to save credentials: {e}")
        
        try:
            self.youtube = build(
                YOUTUBE_API_SERVICE_NAME, 
                YOUTUBE_API_VERSION,
                credentials=credentials
            )
            logger.info("Successfully authenticated with YouTube API")
            return self.youtube
        except Exception as e:
            logger.error(f"Failed to build YouTube service: {e}")
            return None
    
    def check_quota_available(self) -> bool:
        """
        Check if we're likely within daily quota limits
        Returns True if safe to upload, False if approaching limit
        """
        # Reset counter if it's a new day
        today = datetime.now().date()
        if today != self.last_reset_date:
            self.daily_upload_count = 0
            self.last_reset_date = today
        
        # Conservative limit: warn at 5 uploads (leaves buffer before hitting 6)
        if self.daily_upload_count >= 5:
            logger.warning("Approaching daily YouTube quota limit (5+ uploads today)")
            return False
        
        return True
    
    def get_or_create_playlist(self, playlist_name: str = "Smash Bros Matches") -> Optional[str]:
        """
        Get existing playlist ID or create new playlist
        Returns playlist ID or None on failure
        """
        if not self.youtube:
            if not self.get_authenticated_service():
                return None
        
        try:
            # Search for existing playlist
            request = self.youtube.playlists().list(
                part="snippet",
                mine=True,
                maxResults=50
            )
            response = request.execute()
            
            for item in response.get('items', []):
                if item['snippet']['title'] == playlist_name:
                    playlist_id = item['id']
                    logger.info(f"Found existing playlist: {playlist_name} (ID: {playlist_id})")
                    return playlist_id
            
            # Create new playlist if not found
            request = self.youtube.playlists().insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": playlist_name,
                        "description": "Super Smash Bros Ultimate matches recorded with automated capture system"
                    },
                    "status": {
                        "privacyStatus": "public"
                    }
                }
            )
            response = request.execute()
            playlist_id = response['id']
            logger.info(f"Created new playlist: {playlist_name} (ID: {playlist_id})")
            return playlist_id
            
        except HttpError as e:
            logger.error(f"Failed to get/create playlist: {e}")
            return None
    
    def add_video_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        """Add video to playlist"""
        if not self.youtube:
            if not self.get_authenticated_service():
                return False
        
        try:
            request = self.youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id
                        }
                    }
                }
            )
            request.execute()
            logger.info(f"Added video {video_id} to playlist {playlist_id}")
            return True
        except HttpError as e:
            logger.error(f"Failed to add video to playlist: {e}")
            return False
    
    def build_title(self, match_id: int, metadata: Dict) -> str:
        """
        Build video title from match metadata
        Format: "Player1 (Char1) vs Player2 (Char2) - Match #ID"
        Fallback: "Match #ID - YYYY-MM-DD HH:MM"
        """
        try:
            players = metadata.get('players', [])
            if len(players) >= 2:
                # Use first two players for title
                p1 = players[0]
                p2 = players[1]
                p1_name = p1.get('name', 'Unknown')
                p2_name = p2.get('name', 'Unknown')
                p1_char = p1.get('character', '').title()
                p2_char = p2.get('character', '').title()
                
                # Try alternative format: "Player1 (Char1) vs Player2 (Char2) - Match #ID"
                if p1_name and p2_name and p1_char and p2_char:
                    return f"{p1_name} ({p1_char}) vs {p2_name} ({p2_char}) - Match #{match_id}"
        except Exception as e:
            logger.warning(f"Failed to build alternative title format: {e}")
        
        # Fallback to timestamp-based title
        timestamp = metadata.get('timestamp', datetime.now())
        if isinstance(timestamp, datetime):
            date_str = timestamp.strftime("%Y-%m-%d %H:%M")
        else:
            date_str = str(timestamp)
        
        return f"Match #{match_id} - {date_str}"
    
    def build_description(self, match_id: int, metadata: Dict) -> str:
        """Build video description from match metadata"""
        lines = ["Super Smash Bros Ultimate Match", ""]
        
        # Match info
        lines.append(f"Match ID: {match_id}")
        timestamp = metadata.get('timestamp', datetime.now())
        if isinstance(timestamp, datetime):
            lines.append(f"Date: {timestamp.strftime('%B %d, %Y %I:%M %p')}")
        lines.append("")
        
        # Player stats
        players = metadata.get('players', [])
        if players:
            lines.append("Players:")
            for player in players:
                name = player.get('name', 'Unknown')
                char = player.get('character', 'Unknown').title()
                kos = player.get('kos', 0)
                falls = player.get('falls', 0)
                sds = player.get('sds', 0)
                won = player.get('won', False)
                
                winner_tag = " - WINNER" if won else ""
                lines.append(f"- {name} ({char}) - {kos} KOs, {falls} Falls, {sds} SD{winner_tag}")
            lines.append("")
        
        lines.append("Recorded with automated capture system")
        
        return "\n".join(lines)
    
    def build_tags(self, metadata: Dict) -> List[str]:
        """Build video tags from metadata"""
        tags = [
            "super smash bros",
            "smash ultimate",
            "ssbu",
            "gameplay",
            "competitive"
        ]
        
        # Add player names
        players = metadata.get('players', [])
        for player in players:
            name = player.get('name', '').strip()
            if name and name.lower() not in ['unknown', 'player 1', 'player 2', 'player 3', 'player 4']:
                tags.append(name.lower())
        
        # Add character names
        for player in players:
            char = player.get('character', '').strip().lower()
            if char and char != 'unknown':
                tags.append(char)
        
        # Deduplicate and limit to 500 characters total
        tags = list(dict.fromkeys(tags))
        return tags[:30]  # YouTube allows up to 500 total characters
    
    def upload_video(
        self, 
        filepath: str, 
        match_id: int, 
        metadata: Dict,
        playlist_name: Optional[str] = "Smash Bros Matches"
    ) -> Optional[str]:
        """
        Upload video to YouTube
        
        Args:
            filepath: Path to video file
            match_id: Match ID from database
            metadata: Dict with player info, timestamp, etc.
            playlist_name: Name of playlist to add video to (None to skip)
        
        Returns:
            YouTube video URL or None on failure
        """
        if not os.path.exists(filepath):
            logger.error(f"Video file not found: {filepath}")
            return None
        
        # Check quota
        if not self.check_quota_available():
            logger.warning("Skipping upload due to quota concerns")
            return None
        
        # Authenticate
        if not self.youtube:
            if not self.get_authenticated_service():
                logger.error("Failed to authenticate with YouTube")
                return None
        
        try:
            # Build video metadata
            title = self.build_title(match_id, metadata)
            description = self.build_description(match_id, metadata)
            tags = self.build_tags(metadata)
            
            logger.info(f"Uploading video: {title}")
            logger.debug(f"File: {filepath}")
            logger.debug(f"Tags: {tags}")
            
            # Prepare video body
            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "20"  # Gaming category
                },
                "status": {
                    "privacyStatus": "public"
                }
            }
            
            # Create media upload
            media = MediaFileUpload(
                filepath, 
                chunksize=-1,  # Upload entire file in one request
                resumable=True
            )
            
            # Execute upload
            request = self.youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media
            )
            
            response = self._resumable_upload(request)
            
            if not response:
                logger.error("Upload failed - no response from YouTube")
                return None
            
            if 'id' not in response:
                logger.error(f"Upload failed - unexpected response: {response}")
                return None
            
            video_id = response['id']
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            logger.info(f"Successfully uploaded video: {video_url}")
            
            # Increment upload counter
            self.daily_upload_count += 1
            
            # Add to playlist if requested
            if playlist_name:
                if not self.default_playlist_id:
                    self.default_playlist_id = self.get_or_create_playlist(playlist_name)
                
                if self.default_playlist_id:
                    self.add_video_to_playlist(video_id, self.default_playlist_id)
            
            return video_url
            
        except HttpError as e:
            if e.resp.status == 403:
                logger.error("YouTube quota exceeded or permission denied")
            else:
                logger.error(f"HTTP error during upload: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during upload: {e}")
            return None
    
    def _resumable_upload(self, request):
        """
        Execute resumable upload with retry logic
        Returns response dict or None on failure
        """
        response = None
        error = None
        retry = 0
        max_retries = 3
        
        while response is None and retry <= max_retries:
            try:
                status, response = request.next_chunk()
                if response is not None:
                    if 'id' in response:
                        return response
                    else:
                        logger.error(f"Upload failed with unexpected response: {response}")
                        return None
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    error = f"Retriable HTTP error {e.resp.status} occurred"
                    logger.warning(error)
                else:
                    raise
            except Exception as e:
                error = f"Retriable error occurred: {e}"
                logger.warning(error)
            
            if error is not None:
                retry += 1
                if retry > max_retries:
                    logger.error("Max retries exceeded")
                    return None
                
                import time
                import random
                max_sleep = 2 ** retry
                sleep_seconds = random.random() * max_sleep
                logger.info(f"Sleeping {sleep_seconds:.1f} seconds and retrying...")
                time.sleep(sleep_seconds)
        
        return response


# Global uploader instance
_uploader_instance = None

def get_uploader() -> YouTubeUploader:
    """Get or create global uploader instance"""
    global _uploader_instance
    if _uploader_instance is None:
        _uploader_instance = YouTubeUploader()
    return _uploader_instance


def upload_video(filepath: str, match_id: int, metadata: Dict, playlist_name: Optional[str] = "Smash Bros Matches") -> Optional[str]:
    """
    Convenience function to upload a video
    
    Args:
        filepath: Path to video file
        match_id: Match ID from database
        metadata: Dict with 'players' list and 'timestamp'
        playlist_name: Playlist name (None to skip playlist)
    
    Returns:
        YouTube video URL or None on failure
    """
    uploader = get_uploader()
    return uploader.upload_video(filepath, match_id, metadata, playlist_name)


if __name__ == "__main__":
    # Simple test
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) < 2:
        print("Usage: python youtube_uploader.py <video_file>")
        sys.exit(1)
    
    test_file = sys.argv[1]
    test_metadata = {
        'players': [
            {'name': 'TestPlayer1', 'character': 'Mario', 'kos': 3, 'falls': 2, 'sds': 0, 'won': True},
            {'name': 'TestPlayer2', 'character': 'Luigi', 'kos': 2, 'falls': 3, 'sds': 1, 'won': False}
        ],
        'timestamp': datetime.now()
    }
    
    url = upload_video(test_file, 999, test_metadata)
    if url:
        print(f"Test upload successful: {url}")
    else:
        print("Test upload failed")
