#!/usr/bin/env python3
"""
Create dummy test videos for YouTube upload testing
"""

import cv2
import numpy as np
import os
from datetime import datetime, timedelta

def create_dummy_video(filepath: str, duration_seconds: int = 10, text: str = "Test Video"):
    """
    Create a dummy MP4 video with colored frames and text
    
    Args:
        filepath: Output path for the video
        duration_seconds: Video duration in seconds
        text: Text to display on video
    """
    # Video settings
    width = 1920
    height = 1080
    fps = 30
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    
    # Create video writer
    out = cv2.VideoWriter(filepath, fourcc, fps, (width, height))
    
    total_frames = duration_seconds * fps
    
    for frame_num in range(total_frames):
        # Create a colored frame (color changes over time)
        hue = int((frame_num / total_frames) * 180)
        color_hsv = np.uint8([[[hue, 255, 200]]])
        color_bgr = cv2.cvtColor(color_hsv, cv2.COLOR_HSV2BGR)[0][0]
        
        # Create frame with solid color
        frame = np.full((height, width, 3), color_bgr, dtype=np.uint8)
        
        # Add text overlay
        font = cv2.FONT_HERSHEY_SIMPLEX
        
        # Main text
        cv2.putText(frame, text, (50, 200), font, 3, (255, 255, 255), 4, cv2.LINE_AA)
        
        # Frame counter
        frame_text = f"Frame {frame_num + 1}/{total_frames}"
        cv2.putText(frame, frame_text, (50, 300), font, 1.5, (255, 255, 255), 2, cv2.LINE_AA)
        
        # Time counter
        time_elapsed = frame_num / fps
        time_text = f"Time: {time_elapsed:.2f}s / {duration_seconds}s"
        cv2.putText(frame, time_text, (50, 370), font, 1.5, (255, 255, 255), 2, cv2.LINE_AA)
        
        # Add filename at bottom
        filename = os.path.basename(filepath)
        cv2.putText(frame, filename, (50, height - 50), font, 1, (200, 200, 200), 2, cv2.LINE_AA)
        
        # Write frame
        out.write(frame)
    
    out.release()
    print(f"✅ Created: {filepath} ({duration_seconds}s, {total_frames} frames)")


def main():
    """Create test videos with various naming formats"""
    
    print("Creating dummy test videos...\n")
    
    # Test scenarios with different timestamps and match IDs
    test_videos = [
        # Videos with match IDs (simulating videos already in database)
        {
            'dir': 'matches/test_uploads',
            'filename': '1-20240115_143052.mp4',
            'text': 'Match #1\nAnish vs John',
            'duration': 8
        },
        {
            'dir': 'matches/test_uploads',
            'filename': '2-20240115_150230.mp4',
            'text': 'Match #2\nSarah vs Mike',
            'duration': 8
        },
        {
            'dir': 'matches/test_uploads',
            'filename': '3-20240116_120000.mp4',
            'text': 'Match #3\nEmily vs David',
            'duration': 8
        },
        
        # Legacy videos without match IDs (timestamp only)
        {
            'dir': 'matches/forlater',
            'filename': '20240114_100000.mp4',
            'text': 'Legacy Match\nTimestamp Only',
            'duration': 7
        },
        {
            'dir': 'matches/forlater',
            'filename': '20240114_110000.mp4',
            'text': 'Legacy Match\nNo Database ID',
            'duration': 7
        },
        {
            'dir': 'matches/forlater',
            'filename': '20240114_120000.mp4',
            'text': 'Old Match\nTo Be Uploaded',
            'duration': 7
        },
    ]
    
    for video in test_videos:
        filepath = os.path.join(video['dir'], video['filename'])
        create_dummy_video(filepath, video['duration'], video['text'])
    
    print(f"\n{'='*60}")
    print("✅ Test videos created successfully!")
    print(f"{'='*60}")
    print("\nCreated files:")
    print(f"  📁 matches/test_uploads/")
    print(f"     - 1-20240115_143052.mp4  (Match ID: 1)")
    print(f"     - 2-20240115_150230.mp4  (Match ID: 2)")
    print(f"     - 3-20240116_120000.mp4  (Match ID: 3)")
    print(f"  📁 matches/forlater/")
    print(f"     - 20240114_100000.mp4   (Legacy - no match ID)")
    print(f"     - 20240114_110000.mp4   (Legacy - no match ID)")
    print(f"     - 20240114_120000.mp4   (Legacy - no match ID)")
    
    print("\n" + "="*60)
    print("TESTING INSTRUCTIONS")
    print("="*60)
    print("\n1. Test dry run (preview only):")
    print("   python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run")
    
    print("\n2. Test single upload:")
    print("   python bulk_upload_to_youtube.py --directory matches/test_uploads")
    
    print("\n3. Test legacy videos:")
    print("   python bulk_upload_to_youtube.py --directory matches/forlater --dry-run")
    
    print("\n4. Test all directories:")
    print("   python bulk_upload_to_youtube.py --directory matches/test_uploads matches/forlater --dry-run")
    
    print("\nNote: These are dummy videos. You'll need to:")
    print("  - Complete Google Cloud setup (see YOUTUBE_SETUP.md)")
    print("  - Run authentication (browser will open)")
    print("  - (Optional) Create test database records for Match IDs 1, 2, 3")
    print()


if __name__ == "__main__":
    main()
