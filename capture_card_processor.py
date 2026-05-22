import re
import cv2
import numpy as np
import datetime
import os
import threading
import time
import argparse
import json
from enum import Enum
import math
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import shutil
import sys
import traceback
from typing import List, Optional, Dict, Tuple
import pandas as pd
import pytz
import requests
from elo_utils import (
    calculate_elo_update_for_streaming,
    persist_match_participant_elo_diffs,
    update_character_rankings_for_streaming,
    update_inactivity_status,
    update_team_rankings_for_streaming,
)
from gemini_match_analyzer import (
    DEFAULT_GEMINI_MODEL,
    PlayerStats,
    analyze_match_results_video,
    create_gemini_client,
    get_gemini_model,
)
from supabase import create_client, Client
from dotenv import load_dotenv
# YouTube uploads are intentionally disabled for the capture flow.
# from youtube_uploader import upload_video

# Load environment variables from .env file
load_dotenv()

DEFAULT_OUTPUT_DIR = os.path.join("local", "matches")
STARTUP_LOG_MESSAGES = []
CURRENT_LOG_FILEPATH = None


def get_default_audio_backend():
    if sys.platform.startswith("win"):
        return "dshow"
    if sys.platform == "darwin":
        return "avfoundation"
    if sys.platform.startswith("linux"):
        return "pulse"
    return "dshow"


def tail_process_output(output, max_lines=20):
    if not output:
        return ""

    lines = [line for line in output.strip().splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


class StreamToLogger:
    """
    File-like stream that sends print output and tracebacks through logging.
    """
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self._buffer = ""

    def write(self, message):
        if message is None:
            return 0

        message = str(message)
        self._buffer += message

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.logger.log(self.level, line.rstrip())

        return len(message)

    def flush(self):
        if self._buffer:
            self.logger.log(self.level, self._buffer.rstrip())
            self._buffer = ""


class PrefixedMultilineFormatter(logging.Formatter):
    """
    Formatter that repeats the timestamp/level/process/thread/logger prefix on
    traceback continuation lines instead of leaving raw multiline output.
    """
    def format(self, record):
        formatted = super().format(record)
        if "\n" not in formatted:
            return formatted

        continuation_prefix = (
            f"{self.formatTime(record, self.datefmt)}.{int(record.msecs):03d} "
            f"[{record.levelname:<8}] "
            f"[pid={record.process}] "
            f"[thread={record.threadName}] "
            f"[{record.name}] "
        )
        lines = formatted.splitlines()
        return "\n".join([lines[0], *[f"{continuation_prefix}{line}" for line in lines[1:]]])


def log_section(logger, title):
    logger.info("")
    logger.info("")
    logger.info("=" * 96)
    logger.info(title)
    logger.info("=" * 96)


def write_image_or_log(logger, filepath, image, description):
    try:
        if not cv2.imwrite(filepath, image):
            logger.error(f"Failed to write {description}: {filepath}")
            return False
        return True
    except Exception as e:
        logger.exception(f"Error writing {description} to {filepath}: {e}")
        return False


def configure_capture_logging(output_dir):
    global CURRENT_LOG_FILEPATH

    output_dir = os.path.abspath(os.path.normpath(output_dir))
    os.makedirs(output_dir, exist_ok=True)
    log_filepath = os.path.join(output_dir, "smash_capture.log")
    if CURRENT_LOG_FILEPATH == log_filepath and logging.getLogger().handlers:
        return log_filepath

    formatter = PrefixedMultilineFormatter(
        fmt=(
            "%(asctime)s.%(msecs)03d "
            "[%(levelname)-8s] "
            "[pid=%(process)d] "
            "[thread=%(threadName)s] "
            "[%(name)s] "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_filepath,
        mode="a",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.__stdout__)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logging.getLogger('google.auth.transport.requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.captureWarnings(True)

    stdout_logger = logging.getLogger("stdout")
    stderr_logger = logging.getLogger("stderr")
    sys.stdout = StreamToLogger(stdout_logger, logging.INFO)
    sys.stderr = StreamToLogger(stderr_logger, logging.ERROR)

    def log_uncaught_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logging.getLogger(__name__).critical(
            "Uncaught exception; capture process is exiting",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    sys.excepthook = log_uncaught_exception

    if hasattr(threading, "excepthook"):
        def log_thread_exception(args):
            logging.getLogger(__name__).critical(
                f"Uncaught exception in thread {args.thread.name}",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = log_thread_exception

    CURRENT_LOG_FILEPATH = log_filepath
    return log_filepath

class GameState(Enum):
    WAITING = "waiting"
    READY_DETECTED = "ready_detected"
    RECORDING = "recording"
    GAME_END_DETECTED = "game_end_detected"

# Initialize Gemini client
try:
    gemini_client = create_gemini_client()
    gemini_model = get_gemini_model()
except Exception as e:
    STARTUP_LOG_MESSAGES.append((
        "warning",
        f"Failed to initialize Gemini client: {e}",
        traceback.format_exc(),
    ))
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
    STARTUP_LOG_MESSAGES.append((
        "warning",
        f"Failed to initialize Supabase client: {e}",
        traceback.format_exc(),
    ))
    supabase_client = None


def invalidate_frontend_cache():
    """Invalidate the frontend cache after a match is saved"""
    logger = logging.getLogger(__name__)
    frontend_url = os.getenv("FRONTEND_URL")
    if not frontend_url:
        logger.warning("FRONTEND_URL not set, skipping cache invalidation")
        return

    tags = ["players", "matches"]
    try:
        response = requests.get(
            f"{frontend_url}/api/revalidate",
            params={"tags": ",".join(tags)},
            timeout=10
        )
        if response.ok:
            logger.info(f"Frontend cache invalidated: {response.json()}")
        else:
            logger.warning(f"Failed to invalidate cache: {response.status_code} - {response.text}")
    except Exception as e:
        logger.exception(f"Failed to invalidate frontend cache: {e}")


class SmashBrosProcessor:
    def __init__(self, device_index=0, output_dir=DEFAULT_OUTPUT_DIR, test_mode=False, test_video_path=None,
                 center_region_top=0.3, center_region_bottom=0.7, center_region_left=0.1, center_region_right=0.9,
                 game_region_top=0.1, game_region_bottom=0.5, game_region_left=0.2, game_region_right=0.8,
                 consecutive_black_threshold_secs=0.5, play_video=False, video_slowdown_factor=10,
                 rolling_window_days=30, min_free_space_gb=5.0, target_video_size_mb=100.0,
                 audio_enabled=True, audio_device=None, audio_backend=None,
                 audio_sample_rate=None, audio_channels=None, audio_bitrate=None):
        """
        Initialize the Smash Bros match processor
        
        Args:
            device_index: The index of the capture device
            output_dir: Directory to save match recordings
            test_mode: Whether to run in test mode with existing video
            test_video_path: Path to test video file
            center_region_top: Top boundary for center region (0.0-1.0)
            center_region_bottom: Bottom boundary for center region (0.0-1.0)
            center_region_left: Left boundary for center region (0.0-1.0)
            center_region_right: Right boundary for center region (0.0-1.0)
            game_region_top: Top boundary for game region (0.0-1.0)
            game_region_bottom: Bottom boundary for game region (0.0-1.0)
            game_region_left: Left boundary for game region (0.0-1.0)
            game_region_right: Right boundary for game region (0.0-1.0)
            consecutive_black_threshold_secs: Minimum consecutive black screen duration in seconds to detect as a black period
            play_video: Whether to play the video in real-time (test mode only)
            video_slowdown_factor: Factor to slow down result screen videos for better API processing (default: 10)
            rolling_window_days: Number of days to keep match files. Files older than this will be automatically deleted. Default: 30 days.
            min_free_space_gb: Minimum free disk space to keep before starting new video writes. Deletes oldest match files when below this threshold. Default: 5 GB.
            target_video_size_mb: Target size for saved full-match videos after recording. Set to 0 to disable compression.
            audio_enabled: Whether to capture audio during live recording.
            audio_device: ffmpeg audio input device name. Defaults to CAPTURE_AUDIO_DEVICE.
            audio_backend: ffmpeg input backend. Defaults to CAPTURE_AUDIO_BACKEND or the platform default.
            audio_sample_rate: Output audio sample rate. Defaults to CAPTURE_AUDIO_SAMPLE_RATE or 48000.
            audio_channels: Output channel count. Defaults to CAPTURE_AUDIO_CHANNELS or 2.
            audio_bitrate: AAC bitrate used when muxing/compressing. Defaults to CAPTURE_AUDIO_BITRATE or 160k.
        """
        self.device_index = device_index
        self.output_dir = os.path.normpath(output_dir)
        self.test_mode = test_mode
        self.test_video_path = test_video_path
        self.play_video = play_video
        self.video_slowdown_factor = video_slowdown_factor
        self.rolling_window_days = rolling_window_days
        self.min_free_space_bytes = int(min_free_space_gb * 1024 * 1024 * 1024) if min_free_space_gb and min_free_space_gb > 0 else 0
        self.target_video_size_mb = max(0.0, float(target_video_size_mb or 0.0))
        self.audio_enabled = bool(audio_enabled)
        self.audio_device = audio_device or os.getenv("CAPTURE_AUDIO_DEVICE")
        if self.audio_device:
            self.audio_device = self.audio_device.strip().strip("\"'")
        self.audio_backend = (audio_backend or os.getenv("CAPTURE_AUDIO_BACKEND") or get_default_audio_backend()).lower()
        self.audio_sample_rate = int(audio_sample_rate or os.getenv("CAPTURE_AUDIO_SAMPLE_RATE") or 48000)
        self.audio_channels = int(audio_channels or os.getenv("CAPTURE_AUDIO_CHANNELS") or 2)
        self.audio_bitrate = audio_bitrate or os.getenv("CAPTURE_AUDIO_BITRATE") or "160k"
        
        # Region boundaries (as fractions of frame dimensions)
        self.center_region_top = center_region_top
        self.center_region_bottom = center_region_bottom
        self.center_region_left = center_region_left
        self.center_region_right = center_region_right
        
        self.game_region_top = game_region_top
        self.game_region_bottom = game_region_bottom
        self.game_region_left = game_region_left
        self.game_region_right = game_region_right
        
        self.state = GameState.WAITING
        self.cap = None
        self.out = None
        self.current_match_frames = []
        self.frame_buffer = []
        self.buffer_size = 300  # 5 seconds at 60fps
        
        # Detection parameters
        self.black_screen_threshold = 0.1  # Average brightness threshold for black screen
        self.ready_confidence_threshold = 0.38 #0.7
        self.game_end_confidence_threshold = 0.7 # 0.78 # 0.6
        
        # Timing parameters
        self.frames_since_ready = 0
        self.frames_since_black = 0
        self.black_screen_duration_threshold_secs = consecutive_black_threshold_secs  # Use same threshold as black period detection
        self.ready_to_game_timeout = 600  # 10 seconds max from ready to game start
        
        # Consecutive black frame detection
        self.consecutive_black_frames = 0
        self.consecutive_black_threshold_secs = consecutive_black_threshold_secs
        self.in_black_period = False
        self.black_period_start_frame = None
        self.black_period_start_timestamp = None
        self.black_periods = []  # List to store all detected black periods
        
        # Match counter
        self.match_counter = 1
        self.current_match_filepath = None
        self.current_result_screen_filepath = None
        self.current_match_id = None  # Store database match ID
        self.recording_start_time = None
        self.recording_written_frames = 0
        self.current_recording_effective_fps = None
        self.audio_capture_process = None
        self.current_audio_filepath = None
        self.audio_capture_started_at = None
        self.audio_stop_timeout_secs = 10
        
        # Test mode tracking
        self.current_frame_number = 0
        self.game_start_frame = None
        self.game_end_frame = None
        
        # Debug values for display
        self.last_ready_confidence = 0.0
        self.last_game_end_confidence = 0.0
        self.last_avg_brightness = 0.0
        
        # Result screen extraction tracking
        self.recording_frames = []  # Store frames during recording
        self.recording_game_end_scores = []  # Store game end confidence scores during recording
        self.current_recording_frame_index = 0  # Track frame index within current recording
        self.max_recording_frames = 3600  # Limit to ~1 minute at 60fps to prevent memory issues
        self.frame_skip_count = 0
        self.frame_skip_interval = 1  # Keep every result-screen frame by default.
        self.frame_30_image = None  # Store frame 42 (~1.4 seconds at 30fps) for player identification
        self.current_frame_30_image_path = None  # Path to saved frame 42 image file
        self.post_processing_lock = threading.Lock()
        
        # Create output directory
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
        # Create result screens directory
        self.result_screens_dir = os.path.join(self.output_dir, "result_screens")
        if not os.path.exists(self.result_screens_dir):
            os.makedirs(self.result_screens_dir)

        # Store Supabase client as instance variable
        self.supabase_client = supabase_client
        
        # Setup logging
        self.setup_logging()
    
    def setup_logging(self):
        """Setup logging to file and console"""
        log_filepath = configure_capture_logging(self.output_dir)
        self.logger = logging.getLogger(__name__)

        log_section(self.logger, "SMASH CAPTURE SESSION START")
        self.logger.info(f"Log file: {log_filepath}")
        self.logger.info(f"Log rotation: 10 MB per file, 5 backups")
        self.logger.info(f"Test mode: {self.test_mode}")
        self.logger.info(f"Output directory: {self.output_dir}")
        if self.target_video_size_mb > 0:
            self.logger.info(f"Full match video target size: {self.target_video_size_mb:.1f} MB")
        else:
            self.logger.info("Full match video compression: disabled")
        if self.test_mode:
            self.logger.info("Live audio capture: disabled in test mode")
        elif not self.audio_enabled:
            self.logger.info("Live audio capture: disabled by configuration")
        elif not self.audio_device:
            self.logger.info("Live audio capture: disabled; set --audio-device or CAPTURE_AUDIO_DEVICE to enable it")
        else:
            self.logger.info(
                f"Live audio capture: enabled "
                f"(backend={self.audio_backend}, device={self.audio_device}, "
                f"{self.audio_sample_rate} Hz, {self.audio_channels} channel(s), AAC {self.audio_bitrate})"
            )

        for level, message, traceback_text in STARTUP_LOG_MESSAGES:
            getattr(self.logger, level)(f"Startup warning: {message}")
            if traceback_text:
                self.logger.error(traceback_text.rstrip())
    
    def initialize_capture(self):
        """Initialize video capture with exponential backoff retry"""
        if self.test_mode and self.test_video_path:
            self.cap = cv2.VideoCapture(self.test_video_path)
            self.logger.info(f"Initialized test mode with video: {self.test_video_path}")
            # self.cap.set(cv2.CAP_PROP_FPS, 60)
        else:
            # Exponential backoff configuration
            max_retries = 10
            base_delay = 1.0  # seconds
            max_delay = 60.0  # seconds
            
            # Try different backends for capture card
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
            
            for attempt in range(max_retries):
                self.logger.info(f"Capture card detection attempt {attempt + 1}/{max_retries}")
                
                for backend in backends:
                    try:
                        self.cap = cv2.VideoCapture(self.device_index, backend)
                        if self.cap.isOpened():
                            self.logger.info(f"Successfully opened capture device with backend: {backend}")
                            break
                    except Exception as e:
                        self.logger.warning(f"Failed to open capture device with backend {backend}: {e}", exc_info=True)
                        continue
                
                if self.cap and self.cap.isOpened():
                    break
                
                # Calculate delay for exponential backoff
                delay = min(base_delay * (2 ** attempt), max_delay)
                self.logger.warning(f"Capture card not detected, retrying in {delay:.1f} seconds...")
                time.sleep(delay)
            
            if not self.cap or not self.cap.isOpened():
                raise Exception(f"Failed to open capture device after {max_retries} attempts")
            
            # Set capture properties
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            self.cap.set(cv2.CAP_PROP_FPS, 60)
        
        # Get actual properties
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Get FPS from capture device or video file
        if self.test_mode:
            # For test mode (video files), read FPS from file metadata
            reported_fps = self.cap.get(cv2.CAP_PROP_FPS)
            if reported_fps > 0:
                self.fps = int(reported_fps)
            else:
                self.fps = 60
                self.logger.warning(f"FPS not available from video file, using default: {self.fps}")
        else:
            # For live capture, use 30fps (hardcoded for correct playback speed)
            # The device may report incorrectly, so we use a fixed value
            self.fps = 30
            reported_fps = self.cap.get(cv2.CAP_PROP_FPS)
            self.logger.info(f"Using 30fps for recording (device reported: {reported_fps:.1f})")
        
        self.logger.info(f"FPS: {self.fps}")
        self.logger.info(f"Capture initialized at {self.width}x{self.height} @ {self.fps}fps")
    
    def detect_ready_to_fight(self, frame):
        """
        Detect the Super Smash Bros logo (bright yellow/orange circular logo with cross)
        that appears right before the game starts
        """
        try:
            # Convert to HSV for better color detection
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Focus on the center area where the logo appears
            h, w = frame.shape[:2]
            center_region = frame[int(h*self.center_region_top):int(h*self.center_region_bottom), int(w*self.center_region_left):int(w*self.center_region_right)]
            
            # Check if the overall frame is mostly black (characteristic of this screen)
            gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            avg_brightness = np.mean(gray_full) / 255.0
            
            # The screen should be mostly black with a bright logo
            if avg_brightness > 0.15:  # Too bright overall, not the logo screen
                return 0.0, False
            
            # Look for the bright yellow/orange logo in the center region
            center_hsv = cv2.cvtColor(center_region, cv2.COLOR_BGR2HSV)
            
            # Define range for yellow/orange colors (the logo color)
            lower_yellow_orange = np.array([15, 100, 150])  # More restrictive to catch bright yellows/oranges
            upper_yellow_orange = np.array([35, 255, 255])
            
            logo_mask = cv2.inRange(center_hsv, lower_yellow_orange, upper_yellow_orange)
            
            # Calculate the percentage of yellow/orange pixels in center region
            logo_ratio = np.sum(logo_mask > 0) / (logo_mask.shape[0] * logo_mask.shape[1])
            
            # Look for circular/round bright areas (the logo is circular)
            gray_center = cv2.cvtColor(center_region, cv2.COLOR_BGR2GRAY)
            
            # Find very bright areas (the glowing logo)
            bright_mask = gray_center > 180
            bright_ratio = np.sum(bright_mask) / (bright_mask.shape[0] * bright_mask.shape[1])
            
            # Look for the cross pattern within the bright area
            # The cross creates dark lines through the bright circular logo
            if bright_ratio > 0.02:  # Only check for cross if we have enough bright pixels
                # Apply morphological operations to find the cross pattern
                kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (5, 5))
                cross_enhanced = cv2.morphologyEx(bright_mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
                cross_ratio = np.sum(cross_enhanced > 0) / (cross_enhanced.shape[0] * cross_enhanced.shape[1])
            else:
                cross_ratio = 0.0
            
            # Combine all metrics
            # - High logo color ratio
            # - Sufficient bright area (the glowing effect)
            # - Dark background (low overall brightness)
            # - Cross pattern detection
            background_darkness = max(0, (0.15 - avg_brightness) / 0.15)  # Higher score for darker backgrounds
            
            confidence = (logo_ratio * 3 + bright_ratio * 2 + cross_ratio * 1 + background_darkness * 1) / 7
            
            # print(f"Logo detection - Logo ratio: {logo_ratio:.4f}, Bright ratio: {bright_ratio:.4f}, Cross ratio: {cross_ratio:.4f}, Background darkness: {background_darkness:.4f}, Overall brightness: {avg_brightness:.4f}")
            # print(f"Confidence: {confidence:.4f}")
            return confidence, confidence > self.ready_confidence_threshold
        except Exception as e:
            self.logger.exception(f"Error in detect_ready_to_fight: {e}")
            return 0.0, False
    
    def detect_game_end(self, frame):
        """
        Detect game end by looking for 'GAME!' text or victory screen elements
        """
        try:
            # Convert to HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Focus on upper center area where "GAME!" typically appears
            h, w = frame.shape[:2]
            game_region = frame[int(h*self.game_region_top):int(h*self.game_region_bottom), int(w*self.game_region_left):int(w*self.game_region_right)]
            
            # Look for bright yellow/white text (typical of "GAME!" text)
            gray_game = cv2.cvtColor(game_region, cv2.COLOR_BGR2GRAY)
            
            # Look for very bright areas (GAME! text is usually very bright)
            bright_mask = gray_game > 200
            bright_ratio = np.sum(bright_mask) / (bright_mask.shape[0] * bright_mask.shape[1])
            
            # Look for result screen UI elements (usually has specific color patterns)
            # game_hsv = cv2.cvtColor(game_region, cv2.COLOR_BGR2HSV)
            
            # Check for blue UI elements (common in results screen)
            # lower_blue = np.array([100, 50, 50])
            # upper_blue = np.array([130, 255, 255])
            # blue_mask = cv2.inRange(game_hsv, lower_blue, upper_blue)
            # blue_ratio = np.sum(blue_mask > 0) / (blue_mask.shape[0] * blue_mask.shape[1])
            
            # Combine metrics
            confidence = bright_ratio #(bright_ratio + blue_ratio * 0.5)
            
            return confidence, confidence >= self.game_end_confidence_threshold
        except Exception as e:
            self.logger.exception(f"Error in detect_game_end: {e}")
            return 0.0, False
    
    def is_black_screen(self, frame):
        """
        Detect if the frame is mostly black
        """
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            avg_brightness = np.mean(gray) / 255.0
            return avg_brightness, avg_brightness < self.black_screen_threshold
        except Exception as e:
            self.logger.exception(f"Error in is_black_screen: {e}")
            return 0.0, False
    
    def format_timestamp(self, frame_number):
        """
        Convert frame number to timestamp format (HH:MM:SS.mmm)
        """
        if self.fps <= 0:
            return f"Frame {frame_number}"
        
        total_seconds = frame_number / self.fps
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = total_seconds % 60
        
        return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"
    
    def timestamp_to_frame(self, timestamp_str):
        """
        Convert timestamp in mm:ss format to frame number
        """
        try:
            parts = timestamp_str.split(':')
            if len(parts) == 2:
                minutes, seconds = parts
                total_seconds = int(minutes) * 60 + float(seconds)
            elif len(parts) == 3:
                hours, minutes, seconds = parts
                total_seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            else:
                raise ValueError("Invalid timestamp format")
                
            return int(total_seconds * self.fps)
        except (ValueError, IndexError) as e:
            raise ValueError(f"Invalid timestamp format '{timestamp_str}'. Use mm:ss or hh:mm:ss format") from e
    
    def test_threshold_at_timestamp(self, timestamp_str):
        """
        Test detection thresholds at a specific timestamp
        """
        if not self.test_mode or not self.test_video_path:
            self.logger.error("test-threshold requires test mode with video")
            return
        
        try:
            # Initialize capture first to get fps
            self.initialize_capture()
            
            # Now convert timestamp to frame number
            target_frame = self.timestamp_to_frame(timestamp_str)
            print(f"Seeking to timestamp {timestamp_str} (frame {target_frame})")
            
            # Seek to the target frame
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            
            # Read the frame
            ret, frame = self.cap.read()
            if not ret:
                self.logger.error(f"Could not read frame at timestamp {timestamp_str}")
                return
            
            # Get actual frame position (might be slightly different due to keyframes)
            actual_frame = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
            actual_timestamp = self.format_timestamp(actual_frame)
            
            print(f"Actual frame: {actual_frame} ({actual_timestamp})")
            print(f"Frame dimensions: {frame.shape[1]}x{frame.shape[0]}")
            
            # Extract regions used by detection functions
            h, w = frame.shape[:2]
            
            # Center region for ready detection (same as in detect_ready_to_fight)
            center_region = frame[int(h*self.center_region_top):int(h*self.center_region_bottom), int(w*self.center_region_left):int(w*self.center_region_right)]
            
            # Game region for end detection (same as in detect_game_end)
            game_region = frame[int(h*self.game_region_top):int(h*self.game_region_bottom), int(w*self.game_region_left):int(w*self.game_region_right)]
            
            # Get detection values
            ready_confidence, ready_detected = self.detect_ready_to_fight(frame)
            game_end_confidence, game_end_detected = self.detect_game_end(frame)
            avg_brightness, is_black = self.is_black_screen(frame)
            
            # Create output filenames with timestamp
            timestamp_safe = timestamp_str.replace(':', '-')
            center_filename = f"center_region_{timestamp_safe}_frame{actual_frame}.png"
            game_filename = f"game_region_{timestamp_safe}_frame{actual_frame}.png"
            full_filename = f"full_frame_{timestamp_safe}_frame{actual_frame}.png"
            
            # Save the regions
            write_image_or_log(self.logger, center_filename, center_region, "threshold center region")
            write_image_or_log(self.logger, game_filename, game_region, "threshold game region")
            write_image_or_log(self.logger, full_filename, frame, "threshold full frame")
            
            # Print analysis results
            print("\n" + "="*60)
            print(f"THRESHOLD ANALYSIS AT {timestamp_str}")
            print("="*60)
            print(f"Ready Detection:")
            print(f"  Confidence: {ready_confidence:.4f}")
            print(f"  Threshold:  {self.ready_confidence_threshold:.4f}")
            print(f"  Detected:   {'✓ YES' if ready_detected else '✗ NO'}")
            print(f"  Region saved: {center_filename}")
            print()
            print(f"Game End Detection:")
            print(f"  Confidence: {game_end_confidence:.4f}")
            print(f"  Threshold:  {self.game_end_confidence_threshold:.4f}")
            print(f"  Detected:   {'✓ YES' if game_end_detected else '✗ NO'}")
            print(f"  Region saved: {game_filename}")
            print()
            print(f"Black Screen Detection:")
            print(f"  Brightness: {avg_brightness:.4f}")
            print(f"  Threshold:  {self.black_screen_threshold:.4f}")
            print(f"  Is Black:   {'✓ YES' if is_black else '✗ NO'}")
            print()
            print(f"Full Frame saved: {full_filename}")
            print("="*60)
            
            # Create annotated debug image
            debug_frame = frame.copy()
            
            # Draw region boundaries
            cv2.rectangle(debug_frame, (int(w*self.center_region_left), int(h*self.center_region_top)), (int(w*self.center_region_right), int(h*self.center_region_bottom)), (0, 255, 0), 3)  # Center region
            cv2.rectangle(debug_frame, (int(w*self.game_region_left), int(h*self.game_region_top)), (int(w*self.game_region_right), int(h*self.game_region_bottom)), (255, 0, 0), 3)  # Game region
            
            # Add labels
            cv2.putText(debug_frame, "CENTER REGION", (int(w*self.center_region_left), int(h*self.center_region_top)-10), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(debug_frame, "GAME REGION", (int(w*self.game_region_left), int(h*self.game_region_top)-10), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
            
            # Add detection info
            y_pos = 50
            cv2.putText(debug_frame, f"Ready: {ready_confidence:.3f} ({'DETECTED' if ready_detected else 'NOT DETECTED'})", 
                       (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if ready_detected else (255, 255, 255), 2)
            y_pos += 40
            cv2.putText(debug_frame, f"GameEnd: {game_end_confidence:.3f} ({'DETECTED' if game_end_detected else 'NOT DETECTED'})", 
                       (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if game_end_detected else (255, 255, 255), 2)
            y_pos += 40
            cv2.putText(debug_frame, f"Brightness: {avg_brightness:.3f} ({'BLACK' if is_black else 'NOT BLACK'})", 
                       (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0) if is_black else (255, 255, 255), 2)
            
            debug_filename = f"debug_annotated_{timestamp_safe}_frame{actual_frame}.png"
            write_image_or_log(self.logger, debug_filename, debug_frame, "threshold debug frame")
            print(f"Debug annotated frame saved: {debug_filename}")
            
        except ValueError as e:
            self.logger.error(f"Threshold test input error: {e}")
        except Exception as e:
            self.logger.exception(f"Unexpected error while testing threshold: {e}")
        finally:
            self.cleanup()

    def should_capture_audio(self):
        return bool(
            self.audio_enabled
            and not self.test_mode
            and self.audio_device
        )

    def build_audio_capture_command(self, audio_filepath):
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self.logger.warning("ffmpeg not found; audio capture disabled for this match.")
            return None

        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
        ]

        if self.audio_backend == "dshow":
            input_device = self.audio_device
            if not input_device.lower().startswith("audio="):
                input_device = f"audio={input_device}"
            command.extend(["-f", "dshow", "-thread_queue_size", "1024", "-i", input_device])
        elif self.audio_backend == "avfoundation":
            input_device = self.audio_device
            if not input_device.startswith(":"):
                input_device = f":{input_device}"
            command.extend(["-f", "avfoundation", "-thread_queue_size", "1024", "-i", input_device])
        elif self.audio_backend == "pulse":
            command.extend(["-f", "pulse", "-thread_queue_size", "1024", "-i", self.audio_device])
        elif self.audio_backend == "alsa":
            command.extend(["-f", "alsa", "-thread_queue_size", "1024", "-i", self.audio_device])
        else:
            self.logger.warning(f"Unsupported audio backend '{self.audio_backend}'; audio capture disabled for this match.")
            return None

        command.extend([
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(self.audio_sample_rate),
            "-ac",
            str(self.audio_channels),
            audio_filepath,
        ])
        return command

    def start_audio_capture(self, timestamp):
        if not self.should_capture_audio():
            self.audio_capture_process = None
            self.current_audio_filepath = None
            self.audio_capture_started_at = None
            return None

        audio_filepath = os.path.join(self.output_dir, f"{timestamp}.audio.wav")
        command = self.build_audio_capture_command(audio_filepath)
        if not command:
            return None

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.audio_capture_process = process
            self.current_audio_filepath = audio_filepath
            self.audio_capture_started_at = time.monotonic()
            self.logger.info(f"Started audio capture: {os.path.basename(audio_filepath)}")
            return audio_filepath
        except Exception as e:
            self.logger.exception(f"Failed to start audio capture: {e}")
            self.audio_capture_process = None
            self.current_audio_filepath = None
            self.audio_capture_started_at = None
            return None

    def stop_audio_capture(self):
        process = self.audio_capture_process
        audio_filepath = self.current_audio_filepath

        self.audio_capture_process = None
        self.current_audio_filepath = None
        self.audio_capture_started_at = None

        if not process:
            return audio_filepath if audio_filepath and os.path.exists(audio_filepath) else None

        stderr_output = ""
        try:
            if process.poll() is None:
                _, stderr_output = process.communicate(
                    input="q\n",
                    timeout=self.audio_stop_timeout_secs,
                )
            else:
                _, stderr_output = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            self.logger.warning("Audio capture did not stop after quit request; terminating ffmpeg.")
            process.terminate()
            try:
                _, stderr_output = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.logger.warning("Audio capture did not terminate; killing ffmpeg.")
                process.kill()
                _, stderr_output = process.communicate()
        except Exception as e:
            self.logger.exception(f"Error while stopping audio capture: {e}")
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                pass

        stderr_tail = tail_process_output(stderr_output)
        if process.returncode not in (0, None):
            self.logger.warning(
                f"Audio capture exited with return code {process.returncode}."
                + (f" ffmpeg output:\n{stderr_tail}" if stderr_tail else "")
            )
        elif stderr_tail:
            self.logger.info(f"Audio capture ffmpeg output:\n{stderr_tail}")

        if audio_filepath and os.path.exists(audio_filepath):
            try:
                audio_size = os.path.getsize(audio_filepath)
            except OSError as e:
                self.logger.warning(f"Failed to inspect audio file {audio_filepath}: {e}", exc_info=True)
                return None

            if audio_size > 44:
                self.logger.info(
                    f"Stopped audio capture: {os.path.basename(audio_filepath)} "
                    f"({audio_size / (1024 * 1024):.2f} MB)"
                )
                return audio_filepath

            self.logger.warning(f"Audio capture produced an empty file: {audio_filepath}")
            try:
                os.remove(audio_filepath)
            except OSError as e:
                self.logger.warning(f"Failed to remove empty audio file {audio_filepath}: {e}", exc_info=True)
            return None

        self.logger.warning("Audio capture did not produce an audio file.")
        return None

    def mux_audio_into_video(self, video_filepath, audio_filepath):
        if not video_filepath or not audio_filepath:
            return False
        if not os.path.exists(video_filepath):
            self.logger.warning(f"Skipping audio mux; video file does not exist: {video_filepath}")
            return False
        if not os.path.exists(audio_filepath):
            self.logger.warning(f"Skipping audio mux; audio file does not exist: {audio_filepath}")
            return False

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self.logger.warning("ffmpeg not found; cannot mux audio into match video.")
            return False

        temp_filepath = video_filepath + ".with-audio.mp4"
        ffmpeg_cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            video_filepath,
            "-i",
            audio_filepath,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            self.audio_bitrate,
            "-shortest",
            "-movflags",
            "+faststart",
            temp_filepath,
        ]

        try:
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            if result.returncode != 0 or not os.path.exists(temp_filepath):
                self.logger.warning(
                    f"Failed to mux audio into {os.path.basename(video_filepath)} "
                    f"(returncode={result.returncode}): {tail_process_output(result.stderr)}"
                )
                return False

            os.replace(temp_filepath, video_filepath)
            self.logger.info(f"Muxed audio into match video: {os.path.basename(video_filepath)}")
            return True
        except Exception as e:
            self.logger.exception(f"Error while muxing audio into {video_filepath}: {e}")
            return False
        finally:
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError as e:
                    self.logger.warning(f"Failed to remove temporary mux file {temp_filepath}: {e}", exc_info=True)

    def cleanup_audio_file(self, audio_filepath):
        if not audio_filepath or not os.path.exists(audio_filepath):
            return

        try:
            os.remove(audio_filepath)
            self.logger.info(f"Removed temporary audio file: {os.path.basename(audio_filepath)}")
        except OSError as e:
            self.logger.warning(f"Failed to remove temporary audio file {audio_filepath}: {e}", exc_info=True)
    
    def start_match_recording(self):
        """
        Start recording a new match
        """
        # Don't create match in database yet - wait until Gemini processes it and confirms it's eligible
        has_space = self.ensure_free_disk_space(
            "starting match recording",
            excluded_paths=[
                self.current_match_filepath,
                self.current_result_screen_filepath,
                self.current_frame_30_image_path,
            ],
        )

        if not has_space:
            self.logger.error("Skipping match recording because disk space is still below the configured minimum")
            return None
        
        # Use timestamp-only filename initially (will be renamed if eligible)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}.mp4"
        filepath = os.path.join(self.output_dir, filename)
        
        # Use H.264 codec for better compatibility
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.out = cv2.VideoWriter(filepath, fourcc, self.fps, (self.width, self.height))
        
        if not self.out.isOpened():
            self.logger.warning(f"Failed to create video writer for {filepath}")
            return None
        
        # Store current match filepath for metadata addition and potential renaming later
        self.current_match_filepath = filepath
        self.current_result_screen_filepath = None  # Will be set when result screen is saved
        self.recording_start_time = time.monotonic()
        self.recording_written_frames = 0
        self.current_recording_effective_fps = None
        self.start_audio_capture(timestamp)

        audio_status = " with audio" if self.current_audio_filepath else ""
        self.logger.info(f"Started recording: {filename}{audio_status}")
        
        # Write buffered frames (pre-game footage)
        # for buffered_frame in self.frame_buffer:
        #     self.out.write(buffered_frame)
        
        self.current_match_frames = []
        
        # Reset result screen tracking for new match
        self.clear_frame_buffers()
        self.current_recording_frame_index = 0
        self.frame_skip_count = 0
        self.frame_30_image = None  # Reset frame 42 image for new match
        self.current_frame_30_image_path = None  # Reset frame 42 image path for new match
        
        return filepath

    def get_effective_recording_fps(self):
        if self.recording_start_time is None or self.recording_written_frames <= 0:
            return None

        elapsed_seconds = time.monotonic() - self.recording_start_time
        if elapsed_seconds <= 0:
            return None

        return self.recording_written_frames / elapsed_seconds

    def post_process_match_recording(
        self,
        match_filepath: Optional[str],
        recording_frames: List[np.ndarray],
        recording_game_end_scores: List[float],
        frame_30_image: Optional[np.ndarray],
        effective_fps: Optional[float],
        writer_fps: float,
        recording_written_frames: int,
        audio_filepath: Optional[str] = None,
    ):
        """
        Run all slow post-recording work off the capture loop.
        """
        post_processing_lock = getattr(self, "post_processing_lock", None)
        lock_acquired = False
        extraction_fps = writer_fps
        audio_muxed = False

        try:
            if post_processing_lock:
                post_processing_lock.acquire()
                lock_acquired = True

            if effective_fps and writer_fps > 0:
                fps_delta = abs(effective_fps - writer_fps) / writer_fps
                self.logger.info(
                    f"Recording wrote {recording_written_frames} frame(s) at "
                    f"{effective_fps:.2f} effective fps (writer fps: {writer_fps})."
                )
                if fps_delta > 0.05:
                    self.logger.warning(
                        f"Recorded frame rate differed from writer fps by {fps_delta:.0%}; "
                        f"correcting MP4 playback speed to {effective_fps:.2f} fps."
                    )
                    if match_filepath:
                        self.rewrite_video_with_fps(match_filepath, effective_fps)
                    extraction_fps = effective_fps

            if match_filepath:
                if audio_filepath:
                    audio_muxed = self.mux_audio_into_video(match_filepath, audio_filepath)
                self.compress_video_to_target_size(match_filepath)

            self.extract_result_screens(
                recording_frames=recording_frames,
                recording_game_end_scores=recording_game_end_scores,
                frame_30_image=frame_30_image,
                match_filepath=match_filepath,
                source_fps=extraction_fps,
                process_results_in_background=False,
            )

            self.cleanup_old_matches()
        except Exception as e:
            self.logger.exception(f"Error during match post-processing: {e}")
        finally:
            recording_frames.clear()
            recording_game_end_scores.clear()
            import gc
            gc.collect()
            if audio_muxed:
                self.cleanup_audio_file(audio_filepath)
            if lock_acquired:
                post_processing_lock.release()

    def rewrite_video_with_fps(self, filepath: str, output_fps: float) -> bool:
        if output_fps <= 0 or not os.path.exists(filepath):
            return False

        temp_filepath = filepath + ".fpsfix.mp4"
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            self.logger.warning(f"Could not open recorded video for FPS correction: {filepath}")
            return False

        out = None
        try:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if width <= 0 or height <= 0:
                self.logger.warning(f"Could not read recorded video dimensions for FPS correction: {filepath}")
                return False

            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(temp_filepath, fourcc, output_fps, (width, height))
            if not out.isOpened():
                self.logger.warning(f"Could not create FPS-corrected video writer: {temp_filepath}")
                return False

            rewritten_frames = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                out.write(frame)
                rewritten_frames += 1

            if rewritten_frames == 0:
                self.logger.warning(f"No frames were rewritten during FPS correction: {filepath}")
                return False

            out.release()
            out = None
            cap.release()
            cap = None
            os.replace(temp_filepath, filepath)
            self.logger.info(
                f"Corrected recorded video FPS to {output_fps:.2f} using {rewritten_frames} frame(s): "
                f"{os.path.basename(filepath)}"
            )
            return True
        except Exception as e:
            self.logger.exception(f"Failed to correct recorded video FPS for {filepath}: {e}")
            return False
        finally:
            if cap is not None:
                cap.release()
            if out is not None:
                out.release()
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError as e:
                    self.logger.warning(f"Failed to remove temporary FPS correction file {temp_filepath}: {e}", exc_info=True)

    def get_video_duration_seconds(self, filepath: str) -> Optional[float]:
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            self.logger.warning(f"Could not open video to determine duration: {filepath}")
            return None

        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if fps > 0 and frame_count > 0:
                return frame_count / fps
            return None
        finally:
            try:
                cap.release()
            except Exception as e:
                self.logger.warning(f"Failed to release duration probe for {filepath}: {e}", exc_info=True)

    def compress_video_to_target_size(self, filepath: str) -> bool:
        if self.target_video_size_mb <= 0 or not os.path.exists(filepath):
            return False

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self.logger.warning("ffmpeg not found; skipping full match video compression.")
            return False

        target_bytes = int(self.target_video_size_mb * 1024 * 1024)
        original_size = os.path.getsize(filepath)
        if original_size <= target_bytes:
            self.logger.info(
                f"Full match video already under target size: "
                f"{original_size / (1024 * 1024):.1f} MB <= {self.target_video_size_mb:.1f} MB"
            )
            return False

        duration_seconds = self.get_video_duration_seconds(filepath)
        if not duration_seconds or duration_seconds <= 0:
            self.logger.warning(f"Could not determine duration for compression: {filepath}")
            return False

        temp_filepath = filepath + ".compressed.mp4"
        target_video_bits = target_bytes * 8 * 0.90
        bitrate_kbps = max(250, int(target_video_bits / duration_seconds / 1000))
        best_temp_path = None
        best_temp_size = None

        encoder_options = [
            ["-c:v", "libx264", "-preset", "veryfast"],
            ["-c:v", "mpeg4"],
        ]

        self.logger.info(
            f"Compressing full match video from {original_size / (1024 * 1024):.1f} MB "
            f"toward {self.target_video_size_mb:.1f} MB "
            f"({duration_seconds:.1f}s, starting bitrate {bitrate_kbps} kbps)."
        )

        try:
            for encoder_args in encoder_options:
                current_bitrate_kbps = bitrate_kbps

                for attempt in range(3):
                    if os.path.exists(temp_filepath):
                        os.remove(temp_filepath)

                    ffmpeg_cmd = [
                        ffmpeg_path,
                        "-y",
                        "-i",
                        filepath,
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        *encoder_args,
                        "-b:v",
                        f"{current_bitrate_kbps}k",
                        "-maxrate",
                        f"{current_bitrate_kbps}k",
                        "-bufsize",
                        f"{current_bitrate_kbps * 2}k",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-b:a",
                        self.audio_bitrate,
                        "-movflags",
                        "+faststart",
                        temp_filepath,
                    ]

                    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                    if result.returncode != 0 or not os.path.exists(temp_filepath):
                        encoder_name = encoder_args[1] if len(encoder_args) > 1 else "unknown"
                        self.logger.warning(
                            f"ffmpeg compression failed with {encoder_name} "
                            f"(returncode={result.returncode}): {result.stderr.strip()}"
                        )
                        break

                    output_size = os.path.getsize(temp_filepath)
                    if best_temp_size is None or output_size < best_temp_size:
                        if best_temp_path and os.path.exists(best_temp_path):
                            os.remove(best_temp_path)
                        best_temp_path = filepath + ".best-compressed.mp4"
                        os.replace(temp_filepath, best_temp_path)
                        best_temp_size = output_size
                    else:
                        os.remove(temp_filepath)

                    if output_size <= target_bytes:
                        os.replace(best_temp_path, filepath)
                        self.logger.info(
                            f"Compressed full match video to {output_size / (1024 * 1024):.1f} MB: "
                            f"{os.path.basename(filepath)}"
                        )
                        return True

                    next_bitrate_kbps = max(
                        250,
                        int(current_bitrate_kbps * (target_bytes / output_size) * 0.92),
                    )
                    if next_bitrate_kbps >= current_bitrate_kbps:
                        break

                    self.logger.info(
                        f"Compressed output was {output_size / (1024 * 1024):.1f} MB; "
                        f"retrying at {next_bitrate_kbps} kbps."
                    )
                    current_bitrate_kbps = next_bitrate_kbps

            if best_temp_path and best_temp_size and best_temp_size < original_size:
                os.replace(best_temp_path, filepath)
                self.logger.warning(
                    f"Compressed full match video to {best_temp_size / (1024 * 1024):.1f} MB, "
                    f"but did not reach the {self.target_video_size_mb:.1f} MB target."
                )
                return True

            self.logger.warning("Compression did not produce a smaller file; keeping original.")
            return False
        except Exception as e:
            self.logger.exception(f"Error while compressing full match video {filepath}: {e}")
            return False
        finally:
            for cleanup_path in (temp_filepath, best_temp_path):
                if cleanup_path and os.path.exists(cleanup_path):
                    try:
                        os.remove(cleanup_path)
                    except OSError as e:
                        self.logger.warning(f"Failed to remove temporary compression file {cleanup_path}: {e}", exc_info=True)
    
    def clear_frame_buffers(self):
        """
        Clear frame buffers to free memory
        """
        # Clear recording frames
        if self.recording_frames:
            self.recording_frames.clear()
        if self.recording_game_end_scores:
            self.recording_game_end_scores.clear()
        
        # Clear frame buffer if it's too large
        if len(self.frame_buffer) > self.buffer_size // 2:
            self.frame_buffer = self.frame_buffer[-self.buffer_size // 2:]
        
        # Force garbage collection
        import gc
        gc.collect()
    
    def stop_match_recording(self):
        """
        Stop recording current match
        """
        if self.out:
            self.out.release()
            self.out = None
            self.logger.info("Stopped recording")

            effective_fps = self.get_effective_recording_fps()
            match_filepath = self.current_match_filepath
            writer_fps = self.fps
            recording_written_frames = self.recording_written_frames
            recording_frames = self.recording_frames
            recording_game_end_scores = self.recording_game_end_scores
            frame_30_image = self.frame_30_image.copy() if self.frame_30_image is not None else None
            audio_filepath = self.stop_audio_capture()

            # Hand ownership of the just-finished match buffers to the worker
            # and leave the capture loop ready to detect the next match.
            self.recording_frames = []
            self.recording_game_end_scores = []
            self.recording_start_time = None
            self.recording_written_frames = 0
            self.current_recording_effective_fps = None
            self.frame_skip_count = 0
            self.frame_30_image = None
            self.current_frame_30_image_path = None
            self.clear_frame_buffers()
            
            self.match_counter += 1
            self.current_match_id = None  # Reset match ID for next match

            post_process_args = (
                match_filepath,
                recording_frames,
                recording_game_end_scores,
                frame_30_image,
                effective_fps,
                writer_fps,
                recording_written_frames,
                audio_filepath,
            )

            if self.test_mode:
                self.post_process_match_recording(*post_process_args)
            else:
                post_process_thread = threading.Thread(
                    target=self.post_process_match_recording,
                    args=post_process_args,
                    daemon=True,
                    name="match-post-processing",
                )
                post_process_thread.start()
                self.logger.info("Match post-processing started in background thread")

    def get_disk_free_bytes(self):
        """
        Return free bytes for the filesystem containing the output directory.
        """
        usage_path = self.output_dir if os.path.exists(self.output_dir) else os.path.dirname(os.path.abspath(self.output_dir))
        return shutil.disk_usage(usage_path).free

    def iter_cleanup_candidates(self, excluded_paths=None):
        """
        Yield old video files that are safe to delete under disk pressure.
        """
        excluded_paths = {os.path.abspath(path) for path in (excluded_paths or []) if path}
        candidate_dirs = [self.output_dir, self.result_screens_dir]

        for directory in candidate_dirs:
            if not os.path.exists(directory):
                continue

            for filename in os.listdir(directory):
                filepath = os.path.join(directory, filename)
                absolute_path = os.path.abspath(filepath)

                if absolute_path in excluded_paths:
                    continue

                if not os.path.isfile(filepath) or not filename.endswith(('.mp4', '.audio.wav')):
                    continue

                try:
                    yield {
                        "path": filepath,
                        "name": filename,
                        "mtime": os.path.getmtime(filepath),
                        "size": os.path.getsize(filepath),
                    }
                except OSError as e:
                    self.logger.warning(f"Failed to inspect cleanup candidate {filename}: {e}", exc_info=True)

    def ensure_free_disk_space(self, reason="disk space check", excluded_paths=None):
        """
        Delete the oldest stored match videos until enough free disk space exists.
        """
        if self.test_mode or self.min_free_space_bytes <= 0:
            return True

        try:
            free_bytes = self.get_disk_free_bytes()

            if free_bytes >= self.min_free_space_bytes:
                return True

            target_gb = self.min_free_space_bytes / (1024 * 1024 * 1024)
            free_gb = free_bytes / (1024 * 1024 * 1024)
            self.logger.warning(
                f"Low disk space before {reason}: {free_gb:.2f} GB free, target is {target_gb:.2f} GB. Deleting oldest match files."
            )

            deleted_count = 0
            deleted_size = 0
            candidates = sorted(
                self.iter_cleanup_candidates(excluded_paths=excluded_paths),
                key=lambda candidate: candidate["mtime"]
            )

            for candidate in candidates:
                if free_bytes >= self.min_free_space_bytes:
                    break

                try:
                    os.remove(candidate["path"])
                    deleted_count += 1
                    deleted_size += candidate["size"]
                    free_bytes += candidate["size"]
                    age_days = (datetime.datetime.now().timestamp() - candidate["mtime"]) / 86400
                    self.logger.info(
                        f"Deleted oldest match file under disk pressure: {candidate['name']} (age: {age_days:.1f} days)"
                    )
                except OSError as e:
                    self.logger.warning(f"Failed to delete file {candidate['name']} during disk-pressure cleanup: {e}", exc_info=True)

            final_free_bytes = self.get_disk_free_bytes()
            final_free_gb = final_free_bytes / (1024 * 1024 * 1024)
            freed_mb = deleted_size / (1024 * 1024)

            if final_free_bytes < self.min_free_space_bytes:
                self.logger.warning(
                    f"Disk space is still below target after cleanup: {final_free_gb:.2f} GB free. Deleted {deleted_count} file(s), freed {freed_mb:.2f} MB."
                )
                return False
            elif deleted_count > 0:
                self.logger.info(
                    f"Disk-pressure cleanup complete: Deleted {deleted_count} file(s), freed {freed_mb:.2f} MB, {final_free_gb:.2f} GB free."
                )
            return True
        except Exception as e:
            self.logger.exception(f"Error during disk-pressure cleanup: {e}")
            return False
    
    def cleanup_old_matches(self):
        """
        Delete match files older than the rolling window threshold.
        Cleans up both match files and result screen files.
        Skips cleanup in test mode.
        """
        if self.test_mode:
            return  # Don't delete files during testing
        
        if self.rolling_window_days is None or self.rolling_window_days <= 0:
            return  # Skip cleanup if disabled (0 or None)
        
        try:
            cutoff_time = datetime.datetime.now() - datetime.timedelta(days=self.rolling_window_days)
            cutoff_timestamp = cutoff_time.timestamp()
            
            deleted_count = 0
            deleted_size = 0
            
            # Clean up match files in main directory
            if os.path.exists(self.output_dir):
                for filename in os.listdir(self.output_dir):
                    filepath = os.path.join(self.output_dir, filename)
                    # Skip directories and unrelated files
                    if not os.path.isfile(filepath) or not filename.endswith(('.mp4', '.audio.wav')):
                        continue
                    try:
                        file_mtime = os.path.getmtime(filepath)
                        if file_mtime < cutoff_timestamp:
                            file_size = os.path.getsize(filepath)
                            os.remove(filepath)
                            deleted_count += 1
                            deleted_size += file_size
                            self.logger.info(f"Deleted old match file: {filename} (age: {(datetime.datetime.now().timestamp() - file_mtime) / 86400:.1f} days)")
                    except OSError as e:
                        self.logger.warning(f"Failed to delete old match file {filename}: {e}", exc_info=True)
            
            # Clean up result screen artifacts
            if os.path.exists(self.result_screens_dir):
                for filename in os.listdir(self.result_screens_dir):
                    filepath = os.path.join(self.result_screens_dir, filename)
                    if not os.path.isfile(filepath):
                        continue

                    try:
                        file_mtime = os.path.getmtime(filepath)
                        if file_mtime < cutoff_timestamp:
                            file_size = os.path.getsize(filepath)
                            os.remove(filepath)
                            deleted_count += 1
                            deleted_size += file_size
                            self.logger.info(f"Deleted old result screen artifact: {filename} (age: {(datetime.datetime.now().timestamp() - file_mtime) / 86400:.1f} days)")
                    except OSError as e:
                        self.logger.warning(f"Failed to delete old result screen artifact {filename}: {e}", exc_info=True)

            if deleted_count > 0:
                size_mb = deleted_size / (1024 * 1024)
                self.logger.info(f"Cleanup complete: Deleted {deleted_count} file(s), freed {size_mb:.2f} MB")
            else:
                self.logger.debug(f"Cleanup complete: No files older than {self.rolling_window_days} days found")
                
        except Exception as e:
            self.logger.exception(f"Error during cleanup: {e}")

    def save_result_screen_debug_artifacts(
        self,
        result_frames,
        best_frame_index,
        best_confidence,
        reason,
        match_filepath=None,
        frame_30_image=None,
        source_fps=None,
        recording_frame_count=None,
        allow_current_frame_fallback=True,
    ):
        """
        Save enough context to inspect result-screen detections that were too short
        for normal Gemini processing.
        """
        if not result_frames:
            return None

        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"{timestamp}_result_screen"
            video_path = os.path.join(self.result_screens_dir, f"{base_name}.mp4")

            has_space = self.ensure_free_disk_space(
                "writing result screen diagnostic",
                excluded_paths=[match_filepath, self.current_match_filepath, video_path],
            )

            if not has_space:
                self.logger.error("Skipping result screen diagnostic because disk space is still below the configured minimum")
                return None

            result_fps = self.get_result_screen_output_fps(source_fps)
            height, width = result_frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(video_path, fourcc, result_fps, (width, height))

            if out.isOpened():
                for frame in result_frames:
                    out.write(frame)
                out.release()
            else:
                self.logger.warning(f"Failed to create result screen diagnostic video writer for {video_path}")
                video_path = None

            frame_42_path = None
            if frame_30_image is None and allow_current_frame_fallback:
                frame_30_image = self.frame_30_image
            if frame_30_image is not None:
                frame_42_path = os.path.join(self.result_screens_dir, f"{base_name}_frame_42.png")
                write_image_or_log(self.logger, frame_42_path, frame_30_image, "result screen diagnostic frame 42")

            manifest_path = os.path.join(self.result_screens_dir, f"{base_name}_debug.json")
            manifest = {
                "reason": reason,
                "created_at": datetime.datetime.now().isoformat(),
                "frame_count": len(result_frames),
                "minimum_required_frames": self.get_min_result_screen_frames(source_fps),
                "source_fps": source_fps or self.fps,
                "output_fps": result_fps,
                "frame_skip_interval": self.frame_skip_interval,
                "duration_seconds": len(result_frames) / result_fps if result_fps > 0 else None,
                "best_frame_index": int(best_frame_index),
                "best_confidence": float(best_confidence),
                "recording_frame_count": recording_frame_count if recording_frame_count is not None else len(self.recording_frames),
                "current_frame_number": self.current_frame_number,
                "current_match_filepath": match_filepath or self.current_match_filepath,
                "video_path": video_path,
                "frame_42_path": frame_42_path,
                "game_end_confidence_threshold": self.game_end_confidence_threshold,
            }

            with open(manifest_path, "w", encoding="utf-8") as manifest_file:
                json.dump(manifest, manifest_file, indent=2)

            self.logger.warning(
                f"Saved result screen diagnostic artifacts: {os.path.basename(video_path) if video_path else 'video unavailable'}, {os.path.basename(manifest_path)}"
            )
            return manifest_path
        except Exception as e:
            self.logger.exception(f"Failed to save result screen diagnostic: {e}")
            return None

    def get_result_screen_output_fps(self, source_fps=None):
        """
        Result frames are sampled every frame_skip_interval frames. Write clips at
        the effective FPS so the saved result-screen video reflects real time.
        """
        fps = source_fps if source_fps is not None else self.fps
        if fps <= 0:
            return 30

        return max(1, fps / max(1, self.frame_skip_interval))

    def get_min_result_screen_frames(self, source_fps=None):
        """
        Require about 0.5 seconds of result-screen footage after downsampling.
        """
        return max(1, math.ceil(0.5 * self.get_result_screen_output_fps(source_fps)))
    
    def extract_result_screens(
        self,
        recording_frames=None,
        recording_game_end_scores=None,
        frame_30_image=None,
        match_filepath=None,
        source_fps=None,
        process_results_in_background=True,
    ):
        """
        Extract and save result screen frames from the recorded match
        """
        using_current_recording = recording_frames is None and recording_game_end_scores is None
        if recording_frames is None:
            recording_frames = self.recording_frames
        if recording_game_end_scores is None:
            recording_game_end_scores = self.recording_game_end_scores
        if frame_30_image is None and using_current_recording:
            frame_30_image = self.frame_30_image
        if match_filepath is None:
            match_filepath = self.current_match_filepath
        if source_fps is None:
            source_fps = self.fps

        if not recording_frames or not recording_game_end_scores:
            self.logger.warning("No recorded frames or game end scores to process for result screens")
            return None, None
        
        self.logger.info(f"Analyzing {len(recording_frames)} recorded frames for result screen extraction...")
        
        # Find the last frame with highest game end confidence above threshold
        best_frame_index = -1
        best_confidence = 0.0
        
        # Search backwards through the scores to find the last frame with high confidence
        for i in range(len(recording_game_end_scores) - 1, -1, -1):
            confidence = recording_game_end_scores[i]
            if confidence >= self.game_end_confidence_threshold and confidence > best_confidence:
                best_confidence = confidence
                best_frame_index = i
                break  # We want the last (most recent) frame with high confidence
        
        if best_frame_index == -1:
            self.logger.warning("No frame found with game end confidence above threshold for result screens")
            return None, None
        
        # Extract frames from the best frame to the end
        result_frames = recording_frames[best_frame_index:]
        
        min_result_screen_frames = self.get_min_result_screen_frames(source_fps)
        if len(result_frames) < min_result_screen_frames:
            self.logger.warning(
                f"Result screen sequence too short ({len(result_frames)} frames, minimum {min_result_screen_frames}), skipping"
            )
            self.save_result_screen_debug_artifacts(
                result_frames,
                best_frame_index,
                best_confidence,
                reason=f"result screen sequence too short ({len(result_frames)} frames, minimum {min_result_screen_frames})",
                match_filepath=match_filepath,
                frame_30_image=frame_30_image,
                source_fps=source_fps,
                recording_frame_count=len(recording_frames),
                allow_current_frame_fallback=using_current_recording,
            )
            return None, None
        
        # Create result screen video filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Use timestamp-only filename initially (will be renamed if eligible)
        result_filename = f"{timestamp}_result_screen.mp4"
        result_filepath = os.path.join(self.result_screens_dir, result_filename)
        
        if match_filepath == self.current_match_filepath:
            self.current_result_screen_filepath = result_filepath

        has_space = self.ensure_free_disk_space(
            "writing result screen video",
            excluded_paths=[match_filepath, self.current_match_filepath, result_filepath],
        )

        if not has_space:
            self.logger.error("Skipping result screen extraction because disk space is still below the configured minimum")
            return None, None
        
        # Create video writer for result screens
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        result_fps = self.get_result_screen_output_fps(source_fps)
        result_out = cv2.VideoWriter(result_filepath, fourcc, result_fps, (self.width, self.height))
        
        if not result_out.isOpened():
            self.logger.warning(f"Failed to create result screen video writer for {result_filepath}")
            return None, None
        
        # Write result screen frames
        for frame in result_frames:
            result_out.write(frame)
        
        result_out.release()
        
        # Save frame 42 image if available (for player identification)
        # Save it with the same base name as the result screen video for easy matching
        frame_30_image_path = None
        if frame_30_image is not None:
            # Use the same base name as the result screen video
            result_base_name = os.path.splitext(result_filename)[0]  # Remove .mp4 extension
            frame_30_image_path = os.path.join(self.result_screens_dir, f"{result_base_name}_frame_42.png")
            if write_image_or_log(self.logger, frame_30_image_path, frame_30_image, "result screen frame 42"):
                self.logger.info(f"Saved frame 42 image: {os.path.basename(frame_30_image_path)}")
        
        # Calculate duration
        duration_seconds = len(result_frames) / result_fps if result_fps > 0 else 0
        
        self.logger.info(f"Saved result screens: {result_filename}")
        self.logger.info(f"  Duration: {duration_seconds:.2f} seconds ({len(result_frames)} frames)")
        self.logger.info(f"  Output FPS: {result_fps:.2f} (source FPS: {source_fps}, frame skip: {self.frame_skip_interval})")
        self.logger.info(f"  Starting from frame with confidence: {best_confidence:.3f}")
        self.logger.info(f"  Frame index in match: {best_frame_index}/{len(recording_frames)-1}")
        
        if match_filepath == self.current_match_filepath:
            self.current_frame_30_image_path = frame_30_image_path
        
        # Extract player stats and save to database (only when NOT in test mode)
        if not self.test_mode:
            if process_results_in_background:
                background_thread = threading.Thread(
                    target=self.process_match_results,
                    args=(result_filepath, frame_30_image_path, result_fps, match_filepath),
                    daemon=True,
                    name="match-result-processing",
                )
                background_thread.start()
                self.logger.info("Match results processing started in background thread")
            else:
                self.process_match_results(result_filepath, frame_30_image_path, result_fps, match_filepath)

        return result_filepath, frame_30_image_path

    def process_match_results(
        self,
        result_filepath: str,
        frame_30_image_path: Optional[str],
        result_fps: float,
        match_filepath: Optional[str],
    ):
        try:
            log_section(self.logger, "MATCH RESULT PROCESSING START")

            match_stats = self.get_match_stats(
                result_filepath,
                context_image_path=frame_30_image_path,
                output_fps=result_fps,
                use_current_context_image=False,
            )

            if match_stats and match_stats[0].is_online_match == False:
                if os.path.exists(result_filepath):
                    participant_names = [stat.player_name for stat in match_stats]
                    self.add_metadata_to_mp4(result_filepath, participant_names)

                self.save_match_stats(
                    match_stats,
                    match_filepath=match_filepath,
                    result_screen_filepath=result_filepath,
                    frame_30_image_path=frame_30_image_path,
                )
            else:
                self.logger.error("Failed to extract match stats, skipping database save")
        except Exception as e:
            self.logger.exception(f"Error in background match processing: {e}")
    
    def process_frame(self, frame):
        """
        Process a single frame and update state machine
        """
        self.current_frame_number += 1
        
        # Get detection values for debugging
        ready_confidence, ready_detected = self.detect_ready_to_fight(frame)
        game_end_confidence, game_end_detected = self.detect_game_end(frame)
        avg_brightness, is_black = self.is_black_screen(frame)
        
        # Store for display
        self.last_ready_confidence = ready_confidence
        self.last_game_end_confidence = game_end_confidence
        self.last_avg_brightness = avg_brightness
        
        # Print debug info in test mode (every 30 frames to avoid spam)
        if self.test_mode and self.current_frame_number % 30 == 0:
            timestamp = self.format_timestamp(self.current_frame_number)
            print(f"[DEBUG {timestamp}] Ready: {ready_confidence:.3f}, GameEnd: {game_end_confidence:.3f}, Brightness: {avg_brightness:.3f}, State: {self.state.value}")
        
        # Consecutive black frame detection
        if is_black:
            self.consecutive_black_frames += 1
            
            # Start of a new black period
            if not self.in_black_period and self.consecutive_black_frames >= (self.consecutive_black_threshold_secs * self.fps):
                self.in_black_period = True
                self.black_period_start_frame = self.current_frame_number - int(self.consecutive_black_threshold_secs * self.fps) + 1
                self.black_period_start_timestamp = self.format_timestamp(self.black_period_start_frame)
                
                if self.test_mode:
                    print(f"[BLACK PERIOD START] Frame {self.black_period_start_frame} ({self.black_period_start_timestamp}) - Brightness: {avg_brightness:.3f}")
        else:
            # End of black period
            if self.in_black_period:
                black_period_end_frame = self.current_frame_number - 1
                black_period_end_timestamp = self.format_timestamp(black_period_end_frame)
                
                # Calculate duration
                duration_frames = black_period_end_frame - self.black_period_start_frame + 1
                duration_seconds = duration_frames / self.fps if self.fps > 0 else 0
                
                # Store the black period
                black_period = {
                    'start_frame': self.black_period_start_frame,
                    'end_frame': black_period_end_frame,
                    'start_timestamp': self.black_period_start_timestamp,
                    'end_timestamp': black_period_end_timestamp,
                    'duration_frames': duration_frames,
                    'duration_seconds': duration_seconds
                }
                self.black_periods.append(black_period)
                
                if self.test_mode:
                    print(f"[BLACK PERIOD END] Frame {black_period_end_frame} ({black_period_end_timestamp}) - Duration: {duration_seconds:.2f}s ({duration_frames} frames)")
                
                # Reset black period tracking
                self.in_black_period = False
                self.black_period_start_frame = None
                self.black_period_start_timestamp = None
            
            self.consecutive_black_frames = 0
        
        # Add frame to buffer (for pre-game footage) - but only if we're not recording to save memory
        if self.state != GameState.RECORDING:
            self.frame_buffer.append(frame.copy())
            if len(self.frame_buffer) > self.buffer_size:
                self.frame_buffer.pop(0)
        
        # State machine logic
        if self.state == GameState.WAITING:
            if ready_detected:
                self.logger.info("Detected 'READY TO FIGHT!' - Starting recording immediately...")
                if self.test_mode:
                    timestamp = self.format_timestamp(self.current_frame_number)
                    self.logger.info(f"  [TEST MODE] Ready detected at: {timestamp} (Frame {self.current_frame_number}) - Confidence: {ready_confidence:.3f}")
                
                # Start recording immediately
                recording_path = self.start_match_recording()
                if not recording_path:
                    return
                self.game_start_frame = self.current_frame_number
                if self.test_mode:
                    timestamp = self.format_timestamp(self.current_frame_number)
                    self.logger.info(f"  [TEST MODE] *** GAME START at: {timestamp} (Frame {self.current_frame_number}) ***")
                self.state = GameState.RECORDING
                self.frames_since_black = 0
        
        elif self.state == GameState.READY_DETECTED:
            # This state is no longer used - we go directly to RECORDING
            pass
        
        elif self.state == GameState.RECORDING:
            # Write frame to video
            if self.out:
                self.out.write(frame)
                self.recording_written_frames += 1
            
            # Capture frame 42 (~1.4 seconds at 30fps) for player identification
            # Check BEFORE incrementing, so index 41 = frame 42 (0-indexed)
            if self.current_recording_frame_index == 41:
                self.frame_30_image = frame.copy()
                self.logger.info(f"Captured frame 42 (~1.4 seconds into match) for player identification")
            
            # Store frame and game end confidence for result screen extraction.
            self.frame_skip_count += 1
            if self.frame_skip_count >= self.frame_skip_interval:
                self.recording_frames.append(frame.copy())
                self.recording_game_end_scores.append(game_end_confidence)
                self.frame_skip_count = 0
                
                # Limit memory usage by keeping only the most recent frames
                if len(self.recording_frames) > self.max_recording_frames:
                    # Remove oldest frames in chunks to prevent frequent memory operations
                    chunk_size = min(50, len(self.recording_frames) // 4)
                    self.recording_frames = self.recording_frames[chunk_size:]
                    self.recording_game_end_scores = self.recording_game_end_scores[chunk_size:]
            
            self.current_recording_frame_index += 1
            
            # Check for game end using sustained black screen (3 seconds)
            if is_black:
                self.frames_since_black += 1
                if self.frames_since_black > (self.black_screen_duration_threshold_secs * self.fps):
                    self.logger.info("Detected sustained black screen - ending recording...")
                    self.stop_match_recording()
                    self.game_end_frame = self.current_frame_number
                    if self.test_mode:
                        timestamp = self.format_timestamp(self.current_frame_number)
                        self.logger.info(f"  [TEST MODE] *** GAME END at: {timestamp} (Frame {self.current_frame_number}) *** - Brightness: {avg_brightness:.3f}")
                        if self.game_start_frame:
                            duration_frames = self.game_end_frame - self.game_start_frame
                            duration_seconds = duration_frames / self.fps if self.fps > 0 else 0
                            self.logger.info(f"  [TEST MODE] Match duration: {duration_seconds:.2f} seconds ({duration_frames} frames)")
                    self.state = GameState.WAITING
                    self.frames_since_black = 0
                    self.logger.info("Waiting for next match...")
            else:
                self.frames_since_black = 0
        
        elif self.state == GameState.GAME_END_DETECTED:
            # This state is no longer used - game end is detected directly in RECORDING state
            pass
    
    def run(self):
        """
        Main processing loop
        """
        log_section(self.logger, "CAPTURE LOOP START")
        self.logger.info("Starting Smash Bros match processor...")
        self.logger.info(f"State: {self.state.value}")
        
        if self.test_mode and not self.play_video:
            self.logger.info("Test mode: Fast offline processing (no video display)")
        elif self.test_mode and self.play_video:
            self.logger.info("Test mode: Real-time video playback")
        
        try:
            self.initialize_capture()
            
            frame_count = 0
            start_time = time.time()
            
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    if self.test_mode:
                        self.logger.info("Reached end of test video")
                        break
                    else:
                        self.logger.warning("Failed to read frame")
                        continue
                
                # Process the frame
                try:
                    self.process_frame(frame)
                except Exception as e:
                    self.logger.exception(
                        f"Unhandled error while processing frame {self.current_frame_number}: {e}"
                    )
                    raise
                frame_count += 1
                
                # Periodic memory cleanup
                if frame_count % 1000 == 0:
                    import gc
                    gc.collect()
                
                # Handle display and timing based on mode
                if self.test_mode and not self.play_video:
                    # Fast offline processing - no display, no delays
                    # Print progress every 1000 frames to show activity
                    if frame_count % 1000 == 0:
                        elapsed = time.time() - start_time
                        fps_processed = frame_count / elapsed if elapsed > 0 else 0
                        self.logger.info(f"Processed {frame_count} frames ({fps_processed:.1f} fps) - State: {self.state.value}")
                else:
                    # Real-time playback or live capture - show display
                    # Create display frame with status (resize to save memory)
                    display_frame = cv2.resize(frame, (960, 540))
                    
                    # Add status overlay
                    status_text = f"State: {self.state.value}"
                    cv2.putText(display_frame, status_text, (10, 30), 
                               cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                    
                    if self.state == GameState.RECORDING:
                        cv2.putText(display_frame, f"RECORDING MATCH {self.match_counter}", (10, 70), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    
                    # Add debug info overlay in test mode
                    if self.test_mode:
                        y_offset = 110 if self.state == GameState.RECORDING else 70
                        
                        # Ready confidence
                        ready_color = (0, 255, 0) if self.last_ready_confidence > self.ready_confidence_threshold else (255, 255, 255)
                        cv2.putText(display_frame, f"Ready: {self.last_ready_confidence:.3f}", (10, y_offset), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, ready_color, 2)
                        
                        # Game end confidence
                        game_end_color = (0, 255, 0) if self.last_game_end_confidence > self.game_end_confidence_threshold else (255, 255, 255)
                        cv2.putText(display_frame, f"GameEnd: {self.last_game_end_confidence:.3f}", (10, y_offset + 35), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, game_end_color, 2)
                        
                        # Brightness
                        brightness_color = (0, 255, 0) if self.last_avg_brightness < self.black_screen_threshold else (255, 255, 255)
                        cv2.putText(display_frame, f"Brightness: {self.last_avg_brightness:.3f}", (10, y_offset + 70), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, brightness_color, 2)
                        
                        # Consecutive black frames
                        black_period_color = (255, 0, 0) if self.in_black_period else (255, 255, 255)
                        cv2.putText(display_frame, f"Black frames: {self.consecutive_black_frames} {'[IN BLACK PERIOD]' if self.in_black_period else ''}", 
                                   (10, y_offset + 105), cv2.FONT_HERSHEY_SIMPLEX, 0.7, black_period_color, 2)
                        
                        # Frame number and timestamp
                        timestamp = self.format_timestamp(self.current_frame_number)
                        cv2.putText(display_frame, f"Frame: {self.current_frame_number} ({timestamp})", (10, y_offset + 140), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
                    
                    # Show preview (already resized)
                    cv2.imshow('Smash Bros Match Processor', display_frame)
                    
                    # Handle key presses and timing
                    if self.test_mode and self.play_video:
                        # Real-time playback - wait for proper frame timing
                        wait_time = max(1, int(1000 / self.fps))
                        key = cv2.waitKey(wait_time) & 0xFF
                    else:
                        # Live capture - minimal delay
                        key = cv2.waitKey(1) & 0xFF
                    
                    if key == ord('q'):
                        break
                    elif key == ord('r'):  # Manual reset
                        print("Manual state reset")
                        self.state = GameState.WAITING
                        if self.out:
                            self.stop_match_recording()
                    
                    # Print progress every 5 seconds for live capture or real-time playback
                    if frame_count % (self.fps * 5) == 0:
                        elapsed = time.time() - start_time
                        print(f"Processed {frame_count} frames in {elapsed:.1f}s - State: {self.state.value}")
        
        finally:
            self.cleanup()
    
    def print_black_periods_summary(self):
        """
        Print a summary of all detected black periods
        """
        if not self.black_periods:
            print("\n[BLACK PERIODS SUMMARY] No black periods detected")
            return
        
        print(f"\n[BLACK PERIODS SUMMARY] Detected {len(self.black_periods)} black periods:")
        print("="*80)
        
        total_black_duration = 0
        for i, period in enumerate(self.black_periods, 1):
            print(f"Period {i:2d}: {period['start_timestamp']} - {period['end_timestamp']} "
                  f"(Duration: {period['duration_seconds']:6.2f}s, Frames: {period['duration_frames']:4d})")
            total_black_duration += period['duration_seconds']
        
        print("="*80)
        print(f"Total black screen time: {total_black_duration:.2f} seconds")
        
        # Calculate video statistics if we have frame info
        if self.current_frame_number > 0 and self.fps > 0:
            total_video_duration = self.current_frame_number / self.fps
            black_percentage = (total_black_duration / total_video_duration) * 100
            print(f"Total video duration: {total_video_duration:.2f} seconds")
            print(f"Black screen percentage: {black_percentage:.1f}%")
        
        print("="*80)
    
    def update_elo(self, rating_a: float,
                   rating_b: float,
                   winner: str,
                   k: int = 32) -> tuple[int, int]:
        """
        Return the new (rating_a, rating_b) after one game.

        Parameters
        ----------
        rating_a : current Elo for Player A
        rating_b : current Elo for Player B
        winner   : 'A', 'B', or 'draw'
        k        : K-factor (default 32)

        Returns
        -------
        tuple[int, int] : New ratings as integers for (Player A, Player B)

        >>> update_elo(1400, 1000, 'A')
        (1403, 997)
        """
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


    def get_player_name_examples(self) -> str:
        """Fetch known player names for the Gemini prompt."""
        fallback = "habeas, shafaq, jmoon, subby, keneru, kento"
        if not self.supabase_client:
            return fallback

        try:
            response = (
                self.supabase_client.table("players")
                .select("display_name")
                .execute()
            )
            names = sorted({
                player.get("display_name", "").strip()
                for player in response.data or []
                if player.get("display_name") and player.get("display_name").strip()
            }, key=str.lower)
            return ", ".join(names) if names else fallback
        except Exception as e:
            self.logger.exception(f"Failed to fetch player names for Gemini prompt: {e}")
            return fallback


    def get_match_stats(
        self,
        match_results_video_filepath: str,
        slowdown_factor: int = None,
        context_image_path: Optional[str] = None,
        output_fps: Optional[float] = None,
        use_current_context_image: bool = True,
    ) -> Optional[List[PlayerStats]]:
        """
        Extract player stats from a match results video using Gemini API
        Includes frame 42 (~1.4 seconds into match) image to help identify players
        """
        if not gemini_client:
            self.logger.warning("Gemini client not available, skipping stats extraction")
            return None
        
        # Use instance slowdown factor if not provided
        if slowdown_factor is None:
            slowdown_factor = self.video_slowdown_factor
        
        try:
            self.logger.info(f"Extracting player stats from result screen video: {match_results_video_filepath}")
            self.logger.info(f"Using Gemini model: {gemini_model}")
            self.logger.info(f"Using video slowdown factor: {slowdown_factor}x")
            context_image = context_image_path
            if context_image is None and use_current_context_image:
                context_image = getattr(self, 'current_frame_30_image_path', None)

            return analyze_match_results_video(
                gemini_client,
                match_results_video_filepath,
                context_image_path=context_image,
                slowdown_factor=slowdown_factor,
                output_fps=output_fps if output_fps is not None else self.fps,
                model=gemini_model,
                logger=self.logger,
                player_name_examples=self.get_player_name_examples(),
            )
        except Exception as e:
            self.logger.exception(f"Error extracting match stats: {e}")
            return None
    
    def get_player(self, player_name: str) -> Optional[dict]:
        """Get or create a player in the database"""
        if not self.supabase_client:
            self.logger.warning(f"Supabase client unavailable while getting/creating player {player_name}")
            return None
        
        try:
            response = (
                self.supabase_client.table("players")
                .upsert({"display_name": player_name}, on_conflict="display_name")
                .execute()
            )
            return response.data[0]
        except Exception as e:
            self.logger.exception(f"Error getting/creating player {player_name}: {e}")
            return None
        
    def update_player_elo(self, player_id: str, elo: int):
        """Update a player's ELO in the database"""
        if not self.supabase_client:
            self.logger.warning(f"Supabase client unavailable while updating player ELO for {player_id}")
            return
        
        self.supabase_client.table("players").update({"elo": elo}).eq("id", player_id).execute()
    
    def create_match(self) -> Optional[int]:
        """Create a new match in the database"""
        if not self.supabase_client:
            self.logger.warning("Supabase client unavailable while creating match")
            return None
        
        try:
            response = (
                self.supabase_client.table("matches")
                .insert({})
                .execute()
            )
            return response.data[0]['id']
        except Exception as e:
            self.logger.exception(f"Error creating match: {e}")
            return None
        
    def save_match_stats(
        self,
        stats: List[PlayerStats],
        match_id: Optional[int] = None,
        match_filepath: Optional[str] = None,
        result_screen_filepath: Optional[str] = None,
        frame_30_image_path: Optional[str] = None,
    ):
        """Save match stats to the database"""
        self.logger.info(f"Parsed match stats: {stats}")
        if not self.supabase_client:
            self.logger.warning("Supabase client not available, skipping database save")
            return
        
        # Check if match should be skipped (eligibility checks)
        match_is_no_contest = all(not stat.has_won for stat in stats)
        if match_is_no_contest:
            self.logger.info("Match is a no contest, skipping database save")
            return
        
        match_has_cpu = any(stat.is_cpu for stat in stats)
        if match_has_cpu:
            self.logger.info("Match has CPU, skipping database save")
            return
        
        # Skip online matches
        if stats and stats[0].is_online_match:
            self.logger.info("Match is online, skipping database save")
            return
        
        match_has_unknown_players = False
        for stat in stats:
            # check if player name matches the following pattern: "Player <Number>" or "P<Number>" or "P <Number>"
            if re.match(r"^Player \d+$", stat.player_name) or re.match(r"^P\d+$", stat.player_name) or re.match(r"^P \d+$", stat.player_name):
                match_has_unknown_players = True
                break
        
        if match_has_unknown_players:
            self.logger.info("Match has unknown players (Player 1,2,3,etc.), skipping database save")
            return

        # Match passed all eligibility checks - NOW create the match record
        try:
            if match_id is None:
                # Create match in database now that we know it's eligible
                match_id = self.create_match()
            
            if match_id is None:
                self.logger.error("Failed to create match in database")
                return
            
            # Rename files to include match ID
            match_filepath, result_screen_filepath, frame_30_image_path = self.rename_match_files(
                match_id,
                match_filepath=match_filepath,
                result_screen_filepath=result_screen_filepath,
                frame_30_image_path=frame_30_image_path,
            )
            
            players = []
            winners = []
            
            print(f"Saving match stats to database (Match ID: {match_id})")
            
            for stat in stats:
                player = self.get_player(stat.player_name)
                if player is None:
                    self.logger.error(f"Skipping participant because player lookup failed: {stat.player_name}")
                    continue
                
                # Save match participant
                response = (
                    self.supabase_client.table("match_participants")
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
            log_section(self.logger, "MATCH RESULTS")
            
            if winners:
                print(f"🏆 Winner(s): {', '.join(winners)}")
            else:
                print("🤝 No Contest")
            
            print("\nPlayer Stats:")
            for player in players:
                status = "🏆 WINNER" if player['has_won'] else ""
                print(f"  {player['name']} ({player['character']}) - KOs: {player['kos']}, Falls: {player['falls']}, SDs: {player['sds']} {status}")
            
            # Update ELO ratings for 1v1 matches
            if len(stats) == 2:
                print("\n1v1 Match detected - Updating ELO ratings:")
                
                old_elo_1 = players[0]['elo']
                old_elo_2 = players[1]['elo']
                
                winner_index = 1 if players[0]['has_won'] else 2
                winner = 'A' if winner_index == 1 else 'B'
                
                # Use shared ELO calculation
                new_elo_1, new_elo_2, elo_metadata = calculate_elo_update_for_streaming(
                    old_elo_1, old_elo_2, winner,
                    players[0]['id'], players[1]['id'],
                    self.supabase_client,
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
                            self.supabase_client,
                        )
                else:
                    print(
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
                        self.supabase_client,
                        match_created_at=datetime.datetime.now(
                            datetime.timezone.utc
                        ),
                    )

                    if new_character_elos:
                        char_elo_1, char_elo_2 = new_character_elos
                        print(
                            f"  Character ELOs: {players[0]['character']} → {char_elo_1} "
                            f"({players[1]['character']} → {char_elo_2})"
                        )
                
                print(f"  {players[0]['name']}: {old_elo_1} → {new_elo_1} ({elo_change_1:+d})")
                print(f"  {players[1]['name']}: {old_elo_2} → {new_elo_2} ({elo_change_2:+d})")

            if len(players) == 4:
                print("\n2v2 Match detected - Updating team ELO ratings:")
                team_elo_result = update_team_rankings_for_streaming(
                    players,
                    self.supabase_client,
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
                    print(
                        "  "
                        f"{' + '.join(winning_names)}: "
                        f"{team_elo_result['old_winning_elo']} → {winning_team['elo']}"
                    )
                    print(
                        "  "
                        f"{' + '.join(losing_names)}: "
                        f"{team_elo_result['old_losing_elo']} → {losing_team['elo']}"
                    )
                else:
                    print("  Team ELO unchanged; match was not eligible.")
            
            # Update inactivity status for all players
            print("\nUpdating inactivity status...")
            update_inactivity_status(self.supabase_client)

            # Invalidate frontend cache so leaderboard updates immediately
            invalidate_frontend_cache()

            print("="*60)
            
            # Add metadata to match video file
            if match_filepath and os.path.exists(match_filepath):
                participant_names = [stat.player_name for stat in stats]
                self.add_metadata_to_mp4(match_filepath, participant_names)
            
            # YouTube upload disabled. Videos stay saved locally under the output directory.
            # if self.current_match_filepath and os.path.exists(self.current_match_filepath):
            #     print("\nUploading match video to YouTube...")
            #
            #     metadata = {
            #         'players': [{
            #             'name': stat.player_name,
            #             'character': stat.smash_character,
            #             'kos': stat.total_kos,
            #             'falls': stat.total_falls,
            #             'sds': stat.total_sds,
            #             'won': stat.has_won
            #         } for stat in stats],
            #         'timestamp': datetime.datetime.now()
            #     }
            #
            #     try:
            #         youtube_url = upload_video(self.current_match_filepath, match_id, metadata)
            #
            #         if youtube_url:
            #             print(f"Video uploaded to YouTube: {youtube_url}")
            #
            #             try:
            #                 self.supabase_client.table("matches").update({
            #                     "youtube_url": youtube_url
            #                 }).eq("id", match_id).execute()
            #                 print("YouTube URL saved to database")
            #             except Exception as e:
            #                 self.logger.error(f"Failed to save YouTube URL to database: {e}")
            #         else:
            #             print("YouTube upload failed. Video saved locally.")
            #     except Exception as e:
            #         self.logger.error(f"Error during YouTube upload: {e}")
            #         print("YouTube upload failed. Video saved locally.")
            
        except Exception as e:
            self.logger.exception(f"Error saving match stats: {e}")
    
    def rename_match_files(
        self,
        match_id: int,
        match_filepath: Optional[str] = None,
        result_screen_filepath: Optional[str] = None,
        frame_30_image_path: Optional[str] = None,
    ):
        """
        Rename match files to include match ID if they exist
        Format: {match_id}-{timestamp}.mp4
        """
        match_filepath = match_filepath or self.current_match_filepath
        result_screen_filepath = result_screen_filepath or self.current_result_screen_filepath
        frame_30_image_path = frame_30_image_path or self.current_frame_30_image_path

        try:
            # Rename main match file
            if match_filepath and os.path.exists(match_filepath):
                old_path = match_filepath
                old_dir = os.path.dirname(old_path)
                old_filename = os.path.basename(old_path)
                
                # Extract timestamp from old filename (format: YYYYMMDD_HHMMSS.mp4)
                if old_filename.endswith('.mp4'):
                    timestamp_part = old_filename[:-4]  # Remove .mp4
                    if not timestamp_part.startswith(f"{match_id}-"):
                        new_filename = f"{match_id}-{timestamp_part}.mp4"
                    else:
                        new_filename = old_filename
                    new_path = os.path.join(old_dir, new_filename)
                    
                    if old_path != new_path:
                        os.rename(old_path, new_path)
                        match_filepath = new_path
                        if old_path == self.current_match_filepath:
                            self.current_match_filepath = new_path
                        self.logger.info(f"Renamed match file: {old_filename} -> {new_filename}")
            
            # Rename result screen file
            if result_screen_filepath and os.path.exists(result_screen_filepath):
                old_path = result_screen_filepath
                old_dir = os.path.dirname(old_path)
                old_filename = os.path.basename(old_path)
                
                # Extract timestamp from old filename (format: YYYYMMDD_HHMMSS_result_screen.mp4)
                if old_filename.endswith('_result_screen.mp4'):
                    timestamp_part = old_filename[:-len('_result_screen.mp4')]
                    if not timestamp_part.startswith(f"{match_id}-"):
                        new_filename = f"{match_id}-{timestamp_part}_result_screen.mp4"
                    else:
                        new_filename = old_filename
                    new_path = os.path.join(old_dir, new_filename)
                    
                    if old_path != new_path:
                        os.rename(old_path, new_path)
                        result_screen_filepath = new_path
                        if old_path == self.current_result_screen_filepath:
                            self.current_result_screen_filepath = new_path
                        self.logger.info(f"Renamed result screen file: {old_filename} -> {new_filename}")
                        
                    # Also rename the frame 42 image if it exists (should have same base name)
                    if frame_30_image_path and os.path.exists(frame_30_image_path):
                        frame_30_old_path = frame_30_image_path
                    else:
                        frame_30_old_path = os.path.join(old_dir, f"{timestamp_part}_result_screen_frame_42.png")

                    if os.path.exists(frame_30_old_path):
                        frame_30_new_path = os.path.join(old_dir, f"{os.path.splitext(new_filename)[0]}_frame_42.png")
                        if frame_30_old_path != frame_30_new_path:
                            os.rename(frame_30_old_path, frame_30_new_path)
                            frame_30_image_path = frame_30_new_path
                            if frame_30_old_path == self.current_frame_30_image_path:
                                self.current_frame_30_image_path = frame_30_new_path
                            self.logger.info(f"Renamed frame 42 image: {os.path.basename(frame_30_old_path)} -> {os.path.basename(frame_30_new_path)}")
                        
        except Exception as e:
            self.logger.exception(f"Error renaming match files: {e}")

        return match_filepath, result_screen_filepath, frame_30_image_path
    
    def add_metadata_to_mp4(self, filepath: str, participants: List[str]):
        """Add participant names to MP4 file metadata"""
        if not participants:
            return
        
        participants_str = ', '.join(participants)
        temp_filepath = filepath + '.temp.mp4'
        
        try:
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-i', filepath,
                '-c', 'copy',
                '-metadata', f'title=Smash Bros Match - {participants_str}',
                '-metadata', f'comment=Participants: {participants_str}',
                '-metadata', f'description=Super Smash Bros Ultimate match with participants: {participants_str}',
                temp_filepath
            ]
            
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                os.replace(temp_filepath, filepath)
                self.logger.info(f"Added metadata to {filepath}: {participants_str}")
            else:
                self.logger.warning(
                    f"Failed to add metadata to {filepath} "
                    f"(returncode={result.returncode}): {result.stderr}"
                )
                if os.path.exists(temp_filepath):
                    os.remove(temp_filepath)
        except Exception as e:
            self.logger.exception(f"Error adding metadata to {filepath}: {e}")
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except OSError as cleanup_error:
                    self.logger.warning(f"Failed to remove temporary metadata file {temp_filepath}: {cleanup_error}", exc_info=True)

    def cleanup(self):
        """
        Clean up resources
        """
        logger = getattr(self, "logger", logging.getLogger(__name__))
        log_section(logger, "CAPTURE CLEANUP START")
        try:
            # Handle any ongoing black period at the end
            if self.in_black_period:
                black_period_end_frame = self.current_frame_number
                black_period_end_timestamp = self.format_timestamp(black_period_end_frame)

                duration_frames = black_period_end_frame - self.black_period_start_frame + 1
                duration_seconds = duration_frames / self.fps if self.fps > 0 else 0

                black_period = {
                    'start_frame': self.black_period_start_frame,
                    'end_frame': black_period_end_frame,
                    'start_timestamp': self.black_period_start_timestamp,
                    'end_timestamp': black_period_end_timestamp,
                    'duration_frames': duration_frames,
                    'duration_seconds': duration_seconds
                }
                self.black_periods.append(black_period)

                if self.test_mode:
                    print(f"[BLACK PERIOD END] Frame {black_period_end_frame} ({black_period_end_timestamp}) - Duration: {duration_seconds:.2f}s ({duration_frames} frames) [END OF VIDEO]")

            # Print summary of all black periods
            try:
                self.print_black_periods_summary()
            except Exception as e:
                logger.exception(f"Failed to print black periods summary: {e}")
            
            if self.out:
                try:
                    self.out.release()
                except Exception as e:
                    logger.exception(f"Failed to release video writer during cleanup: {e}")
                finally:
                    self.out = None
            if self.audio_capture_process:
                try:
                    audio_filepath = self.stop_audio_capture()
                    if audio_filepath:
                        logger.warning(
                            f"Audio capture stopped during cleanup and was not muxed: {audio_filepath}"
                        )
                except Exception as e:
                    logger.exception(f"Failed to stop audio capture during cleanup: {e}")
            if self.cap:
                try:
                    self.cap.release()
                except Exception as e:
                    logger.exception(f"Failed to release capture device during cleanup: {e}")
                finally:
                    self.cap = None
            try:
                cv2.destroyAllWindows()
            except Exception as e:
                logger.exception(f"Failed to destroy OpenCV windows during cleanup: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error during cleanup: {e}")
        finally:
            logger.info("Capture cleanup complete")


def main():
    parser = argparse.ArgumentParser(description='Super Smash Bros Match Processor')
    parser.add_argument('--test', action='store_true', help='Run in test mode with existing video')
    parser.add_argument('--video', type=str, help='Path to test video file')
    parser.add_argument('--play-video', action='store_true', help='Play video in real-time during test mode (default: fast offline processing)')
    parser.add_argument('--test-threshold', type=str, help='Test detection thresholds at specific timestamp (mm:ss or hh:mm:ss format)')
    parser.add_argument('--device', type=int, default=0, help='Capture device index (default: 0)')
    parser.add_argument('--output', type=str, default=DEFAULT_OUTPUT_DIR, help=f'Output directory (default: {DEFAULT_OUTPUT_DIR})')
    
    # Region boundary arguments
    parser.add_argument('--center-region-top', type=float, default=0.3, help='Top boundary for center region (0.0-1.0, default: 0.3)')
    parser.add_argument('--center-region-bottom', type=float, default=0.7, help='Bottom boundary for center region (0.0-1.0, default: 0.7)')
    parser.add_argument('--center-region-left', type=float, default=0.4, help='Left boundary for center region (0.0-1.0, default: 0.1)')
    parser.add_argument('--center-region-right', type=float, default=0.6, help='Right boundary for center region (0.0-1.0, default: 0.9)')
    
    parser.add_argument('--game-region-top', type=float, default=0.27, help='Top boundary for game region (0.0-1.0, default: 0.1)')
    parser.add_argument('--game-region-bottom', type=float, default=0.54, help='Bottom boundary for game region (0.0-1.0, default: 0.5)')
    parser.add_argument('--game-region-left', type=float, default=0.2, help='Left boundary for game region (0.0-1.0, default: 0.2)')
    parser.add_argument('--game-region-right', type=float, default=0.8, help='Right boundary for game region (0.0-1.0, default: 0.8)')
    
    # Black frame detection arguments
    parser.add_argument('--black-frame-threshold-secs', type=float, default=0.5, help='Minimum consecutive black screen duration in seconds to detect as a black period (default: 0.5)')
    
    # Video processing arguments
    parser.add_argument('--video-slowdown-factor', type=int, default=10, help='Factor to slow down result screen videos for better API processing (default: 10)')
    parser.add_argument('--target-video-size-mb', type=float, default=100.0, help='Target size for saved full match videos after recording (default: 100). Set to 0 to disable compression.')

    # Audio capture arguments
    parser.add_argument('--no-audio', action='store_true', help='Disable live audio capture even if an audio device is configured.')
    parser.add_argument('--audio-device', type=str, default=None, help='ffmpeg audio input device name. Defaults to CAPTURE_AUDIO_DEVICE. On Windows, use ffmpeg -list_devices true -f dshow -i dummy to find it.')
    parser.add_argument('--audio-backend', type=str, default=None, choices=['dshow', 'avfoundation', 'pulse', 'alsa'], help='ffmpeg audio input backend. Defaults to CAPTURE_AUDIO_BACKEND or the platform default.')
    parser.add_argument('--audio-sample-rate', type=int, default=None, help='Audio sample rate for temporary WAV capture. Defaults to CAPTURE_AUDIO_SAMPLE_RATE or 48000.')
    parser.add_argument('--audio-channels', type=int, default=None, help='Number of audio channels to save. Defaults to CAPTURE_AUDIO_CHANNELS or 2.')
    parser.add_argument('--audio-bitrate', type=str, default=None, help='AAC bitrate when muxing audio into MP4. Defaults to CAPTURE_AUDIO_BITRATE or 160k.')
    
    # Rolling window arguments
    parser.add_argument('--rolling-window-days', type=int, default=30, help='Number of days to keep match files. Files older than this will be automatically deleted (default: 30). Set to 0 to disable cleanup.')
    parser.add_argument('--min-free-space-gb', type=float, default=5.0, help='Minimum free disk space before starting new video writes. Deletes oldest match files when below this value (default: 5.0). Set to 0 to disable disk-pressure cleanup.')
    
    args = parser.parse_args()
    configure_capture_logging(args.output)
    cli_logger = logging.getLogger(__name__)
    log_section(cli_logger, "SMASH CAPTURE CLI START")
    cli_logger.info("Command-line arguments parsed")
    
    if args.test and not args.video:
        cli_logger.error("Test mode requires --video parameter")
        return 2
    
    if args.test_threshold and not args.video:
        cli_logger.error("--test-threshold requires --video parameter")
        return 2
    
    # Validate region boundaries
    def validate_region(name, top, bottom, left, right):
        if not (0.0 <= top < bottom <= 1.0):
            cli_logger.error(f"{name} top ({top}) must be < bottom ({bottom}) and both in range 0.0-1.0")
            return False
        if not (0.0 <= left < right <= 1.0):
            cli_logger.error(f"{name} left ({left}) must be < right ({right}) and both in range 0.0-1.0")
            return False
        return True
    
    if not validate_region("Center region", args.center_region_top, args.center_region_bottom, 
                          args.center_region_left, args.center_region_right):
        return 2
    
    if not validate_region("Game region", args.game_region_top, args.game_region_bottom,
                          args.game_region_left, args.game_region_right):
        return 2

    if args.audio_sample_rate is not None and args.audio_sample_rate <= 0:
        cli_logger.error("--audio-sample-rate must be greater than 0")
        return 2

    if args.audio_channels is not None and args.audio_channels <= 0:
        cli_logger.error("--audio-channels must be greater than 0")
        return 2

    try:
        # Create processor
        processor = SmashBrosProcessor(
            device_index=args.device,
            output_dir=args.output,
            test_mode=args.test or bool(args.test_threshold),
            test_video_path=args.video,
            center_region_top=args.center_region_top,
            center_region_bottom=args.center_region_bottom,
            center_region_left=args.center_region_left,
            center_region_right=args.center_region_right,
            game_region_top=args.game_region_top,
            game_region_bottom=args.game_region_bottom,
            game_region_left=args.game_region_left,
            game_region_right=args.game_region_right,
            consecutive_black_threshold_secs=args.black_frame_threshold_secs,
            play_video=args.play_video,
            video_slowdown_factor=args.video_slowdown_factor,
            rolling_window_days=args.rolling_window_days,
            min_free_space_gb=args.min_free_space_gb,
            target_video_size_mb=args.target_video_size_mb,
            audio_enabled=not args.no_audio,
            audio_device=args.audio_device,
            audio_backend=args.audio_backend,
            audio_sample_rate=args.audio_sample_rate,
            audio_channels=args.audio_channels,
            audio_bitrate=args.audio_bitrate,
        )

        # Handle test-threshold mode
        if args.test_threshold:
            processor.test_threshold_at_timestamp(args.test_threshold)
            return 0

        # Run processor
        processor.run()
        return 0
    except KeyboardInterrupt:
        cli_logger.info("Interrupted by user")
        return 130
    except Exception as e:
        cli_logger.exception(f"Fatal error in capture process: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
