import json
import os
import subprocess
import tempfile
import time
from typing import List, Optional

import cv2
from google import genai
from google.genai import types
from pydantic import BaseModel, Field


DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview"
DEFAULT_GEMINI_FALLBACK_MODELS = [
    "gemini-3-pro-preview",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
]
DEFAULT_VIDEO_SAMPLE_FPS = 4.0
DEFAULT_UPLOAD_TIMEOUT_SECONDS = 300
DEFAULT_RESULT_STILL_COUNT = 4
DEFAULT_FILE_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_GEMINI_MAX_RETRIES = 5
DEFAULT_GEMINI_RETRY_DELAY_SECONDS = 300


class GeminiPlayerStats(BaseModel):
    smash_character: str = Field(
        min_length=1,
        description="The Super Smash Bros Ultimate character shown on this player card. Use 'unknown' only when the card is not readable.",
    )
    player_name: str = Field(
        min_length=1,
        description="The player tag shown beside P1/P2/P3/P4 and below the character name. Do not return P1/P2/P3/P4 as a player name.",
    )
    is_cpu: bool = Field(
        description="True only when the player card explicitly says CPU. False for every human player tag.",
    )
    total_kos: int = Field(
        ge=0,
        description="Total KOs shown on the card. If the number is hidden, count the mini character icons under KOs. Never return null.",
    )
    total_falls: int = Field(
        ge=0,
        description="Total falls shown on the card as a non-negative integer. If the screen shows a negative value, return its absolute value.",
    )
    total_sds: int = Field(
        ge=0,
        description="Total self-destructs shown on the card. If the number is hidden, count mini character icons under SDs. Never return null.",
    )
    has_won: bool = Field(
        description="True only for the card with a gold rank 1 winner marker. False for no-contest matches and all non-winners.",
    )


class GeminiMatchStats(BaseModel):
    is_online_match: bool = Field(
        description="True if any player tag is exactly onlineacc. False for offlineacc or normal player tags.",
    )
    players: List[GeminiPlayerStats] = Field(
        min_length=1,
        max_length=8,
        description="One item for every visible player card in the results screen.",
    )


class PlayerStats(GeminiPlayerStats):
    is_online_match: bool = Field(
        description="Match-level online flag copied onto each player for downstream database logic.",
    )


def create_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")

    api_version = os.getenv("GEMINI_API_VERSION", "v1alpha")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version=api_version),
    )


def get_gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def _env_list(name: str, default: List[str]) -> List[str]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return list(default)
    return [value.strip() for value in raw_value.split(",") if value.strip()]


def get_gemini_fallback_models() -> List[str]:
    return _env_list("GEMINI_FALLBACK_MODELS", DEFAULT_GEMINI_FALLBACK_MODELS)


def _ordered_models(primary_model: str) -> List[str]:
    models = []
    for candidate in [primary_model, *get_gemini_fallback_models()]:
        if candidate and candidate not in models:
            models.append(candidate)
    return models


def _log(logger, level: str, message: str):
    if logger is not None:
        getattr(logger, level)(message)
    else:
        print(message)


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _get_video_fps(video_path: str, fallback_fps: int = 30) -> int:
    cap = cv2.VideoCapture(video_path)
    try:
        fps = int(cap.get(cv2.CAP_PROP_FPS))
    finally:
        cap.release()
    return fps if fps > 0 else fallback_fps


def _slow_down_video(
    source_video_path: str,
    output_video_path: str,
    slowdown_factor: int,
    output_fps: int,
):
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-an",
        "-i",
        source_video_path,
        "-vf",
        f"setpts={slowdown_factor}*PTS",
        "-r",
        str(output_fps),
        "-map_metadata",
        "0",
        output_video_path,
        "-loglevel",
        "quiet",
    ]
    subprocess.run(ffmpeg_cmd, check=True)


def _extract_result_stills(
    video_path: str,
    output_dir: str,
    max_stills: int = DEFAULT_RESULT_STILL_COUNT,
) -> List[str]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            return []

        fractions = [0.08, 0.28, 0.52, 0.76, 0.92]
        frame_indexes = []
        for fraction in fractions:
            index = min(total_frames - 1, max(0, int(total_frames * fraction)))
            if index not in frame_indexes:
                frame_indexes.append(index)
            if len(frame_indexes) >= max_stills:
                break

        still_paths = []
        for still_number, frame_index in enumerate(frame_indexes, start=1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
            if not ret:
                continue
            still_path = os.path.join(output_dir, f"result_screen_still_{still_number}.png")
            if cv2.imwrite(still_path, frame):
                still_paths.append(still_path)
        return still_paths
    finally:
        cap.release()


def _file_state_name(file_info) -> str:
    state = getattr(file_info, "state", "")
    value = getattr(state, "value", None)
    if value:
        return str(value).upper()
    name = getattr(state, "name", None)
    if name:
        return str(name).upper()
    return str(state).split(".")[-1].upper()


def _wait_for_file_active(
    client,
    uploaded_file,
    timeout_seconds: int,
    poll_interval_seconds: float,
    logger=None,
):
    deadline = time.monotonic() + timeout_seconds
    last_state = "UNKNOWN"
    poll_interval_seconds = max(1.0, poll_interval_seconds)

    while time.monotonic() < deadline:
        file_info = client.files.get(name=uploaded_file.name)
        last_state = _file_state_name(file_info)
        if last_state == "ACTIVE":
            return file_info
        if last_state == "FAILED":
            error = getattr(file_info, "error", None)
            raise RuntimeError(f"Gemini file processing failed for {uploaded_file.name}: {error}")
        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))

    raise TimeoutError(
        f"Timed out after {timeout_seconds}s waiting for Gemini file {uploaded_file.name} "
        f"to become ACTIVE; last state was {last_state}"
    )


def _upload_and_wait(
    client,
    path: str,
    timeout_seconds: int,
    poll_interval_seconds: float,
    logger=None,
):
    uploaded = client.files.upload(file=path)
    _log(
        logger,
        "info",
        f"Uploaded {os.path.basename(path)} to Gemini; waiting for processing "
        f"(poll every {poll_interval_seconds:.1f}s)...",
    )
    return _wait_for_file_active(client, uploaded, timeout_seconds, poll_interval_seconds, logger)


def _delete_uploaded_files(client, uploaded_files, logger=None):
    for uploaded_file in uploaded_files:
        if uploaded_file is None:
            continue
        try:
            client.files.delete(name=uploaded_file.name)
        except Exception as exc:
            _log(logger, "warning", f"Failed to delete Gemini file {uploaded_file.name}: {exc}")


def _make_file_part(file_info, video_sample_fps: Optional[float] = None):
    file_uri = getattr(file_info, "uri", None)
    mime_type = getattr(file_info, "mime_type", None)
    if not file_uri:
        raise ValueError(f"Uploaded Gemini file {file_info.name} does not have a file URI")

    part_kwargs = {
        "file_data": types.FileData(file_uri=file_uri, mime_type=mime_type),
    }
    if video_sample_fps:
        part_kwargs["video_metadata"] = types.VideoMetadata(fps=video_sample_fps)
    if "media_resolution" in getattr(types.Part, "model_fields", {}) and hasattr(types, "PartMediaResolution"):
        part_kwargs["media_resolution"] = types.PartMediaResolution(
            level=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_HIGH
        )

    return types.Part(**part_kwargs)


def _build_prompt(
    has_player_context_image: bool,
    result_still_count: int,
    player_name_examples: Optional[str] = None,
) -> str:
    player_context_note = ""
    if has_player_context_image:
        player_context_note = """
I included one early-match frame captured around frame 42. Use it only to identify player names when the results screen advances too quickly or the tags are clearer in that frame.
"""

    result_still_note = ""
    if result_still_count > 0:
        result_still_note = f"""
I also included {result_still_count} full-resolution still PNG frame(s) sampled from the results screen. Prefer these still images for OCR of player names, character names, KOs, Falls, SDs, and the winner marker. Use the video to resolve menu transitions or values that appear only briefly.
"""

    player_examples_text = player_name_examples or "habeas, shafaq, jmoon, subby, keneru, and kento"

    return f"""Here is a Super Smash Bros Ultimate results screen capture.
{player_context_note}{result_still_note}
Return exactly one JSON object that matches this shape:

{{
  "is_online_match": boolean,
  "players": [
    {{
      "smash_character": string,
      "player_name": string,
      "is_cpu": boolean,
      "total_kos": integer,
      "total_falls": integer,
      "total_sds": integer,
      "has_won": boolean
    }}
  ]
}}

Rules:
- Return one player object for every visible player card. Do not omit a human player just because their card is partially obscured or the result menu advances quickly.
- Player names are listed beside P1, P2, P3, etc and under the actual Smash character name. Player names are not P1/P2/P3/P4 and are not character names.
- Examples of known player names are {player_examples_text}. These are examples, not a closed list.
- Zelda, Joker, Lucina, Donkey Kong, and similar labels are Smash character names, not player names.
- total_kos, total_falls, and total_sds must be non-negative integers. Never return null.
- If a numeric stat is not visible, count the mini character icons under that stat's section. If neither a number nor icons are visible, return 0.
- If the screen displays a negative Falls value, return the positive absolute value.
- has_won is true only for the card with a gold rank 1 winner marker at the top right. If no player has that marker, this is a no-contest and every player has has_won=false.
- is_online_match is true if any player name is exactly "onlineacc". It is false for "offlineacc" and for all normal player tags.
- is_cpu is true only if the player card explicitly says "CPU". Otherwise it is false.
- If all people playing have player names and no card says CPU, then is_cpu must be false for every player and there should be at least two players.
- If you see "mmmmm" as a player name, it has exactly five m letters.
- If a rectangular player card shows "READY FOR THE NEXT BATTLE" for the entire video instead of KOs, Falls, and SDs, set that card to player_name="unknown", smash_character="unknown", total_kos=0, total_falls=0, total_sds=0, is_cpu=false, and has_won=false.
"""


def _build_generate_config():
    schema = GeminiMatchStats.model_json_schema()
    model_fields = getattr(types.GenerateContentConfig, "model_fields", {})
    config_kwargs = {
        "temperature": 0,
    }

    if "response_format" in model_fields:
        config_kwargs["response_format"] = {
            "text": {
                "mime_type": "application/json",
                "schema": schema,
            },
        }
    elif "response_json_schema" in model_fields:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = schema
    else:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = GeminiMatchStats

    if "media_resolution" in model_fields and hasattr(types, "MediaResolution"):
        config_kwargs["media_resolution"] = types.MediaResolution.MEDIA_RESOLUTION_HIGH

    thinking_config = _build_thinking_config()
    if thinking_config is not None and "thinking_config" in model_fields:
        config_kwargs["thinking_config"] = thinking_config

    return types.GenerateContentConfig(**config_kwargs)


def _build_thinking_config():
    model_fields = getattr(types.ThinkingConfig, "model_fields", {})
    thinking_level = os.getenv("GEMINI_THINKING_LEVEL", "high")

    if "thinking_level" in model_fields:
        return types.ThinkingConfig(thinking_level=thinking_level)

    return None


def _parse_response(response) -> GeminiMatchStats:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiMatchStats):
        return parsed
    if isinstance(parsed, list):
        players = []
        is_online_match = False
        for stat in parsed:
            if isinstance(stat, PlayerStats):
                is_online_match = is_online_match or stat.is_online_match
                players.append(GeminiPlayerStats(**stat.model_dump(exclude={"is_online_match"})))
            elif isinstance(stat, dict):
                is_online_match = is_online_match or bool(stat.get("is_online_match", False))
                stat = {key: value for key, value in stat.items() if key != "is_online_match"}
                players.append(GeminiPlayerStats(**stat))
        return GeminiMatchStats(is_online_match=is_online_match, players=players)
    if isinstance(parsed, dict):
        return GeminiMatchStats.model_validate(parsed)

    text = getattr(response, "text", None)
    if not text:
        raise ValueError("Gemini response did not include parsed JSON or text")

    try:
        return GeminiMatchStats.model_validate_json(text)
    except Exception:
        return GeminiMatchStats.model_validate(json.loads(text))


def _is_rate_limit_error(exc: Exception) -> bool:
    status = getattr(exc, "status", "")
    code = getattr(exc, "code", None)
    message = str(exc)
    return (
        code == 429
        or str(status).upper() == "RESOURCE_EXHAUSTED"
        or "429" in message
        or "RESOURCE_EXHAUSTED" in message
        or "Too Many Requests" in message
    )


def _exception_details(exc: Exception) -> str:
    details = {
        "type": exc.__class__.__name__,
        "message": str(exc),
    }
    for attr_name in ("code", "status", "reason", "details", "metadata", "args"):
        attr_value = getattr(exc, attr_name, None)
        if attr_value:
            details[attr_name] = attr_value

    response = getattr(exc, "response", None)
    if response is not None:
        response_details = {}
        for attr_name in ("status_code", "reason", "text"):
            attr_value = getattr(response, attr_name, None)
            if attr_value:
                response_details[attr_name] = attr_value
        headers = getattr(response, "headers", None)
        if headers:
            response_details["headers"] = dict(headers)
        if response_details:
            details["response"] = response_details

    return json.dumps(details, default=str, ensure_ascii=True)


def _generate_content(
    client,
    *,
    model: str,
    config,
    contents,
):
    return client.models.generate_content(
        model=model,
        config=config,
        contents=contents,
    )


def _to_downstream_player_stats(match_stats: GeminiMatchStats) -> List[PlayerStats]:
    return [
        PlayerStats(
            is_online_match=match_stats.is_online_match,
            **player.model_dump(),
        )
        for player in match_stats.players
    ]


def analyze_match_results_video(
    client,
    result_video_path: str,
    *,
    context_image_path: Optional[str] = None,
    slowdown_factor: int = 10,
    output_fps: Optional[int] = None,
    model: Optional[str] = None,
    logger=None,
    player_name_examples: Optional[str] = None,
) -> Optional[List[PlayerStats]]:
    if not os.path.exists(result_video_path):
        raise FileNotFoundError(f"Result video file not found: {result_video_path}")

    model = model or get_gemini_model()
    slowdown_factor = max(1, int(slowdown_factor or 1))
    output_fps = output_fps or _get_video_fps(result_video_path)
    video_sample_fps = _env_float("GEMINI_VIDEO_SAMPLE_FPS", DEFAULT_VIDEO_SAMPLE_FPS)
    timeout_seconds = _env_int("GEMINI_UPLOAD_TIMEOUT_SECONDS", DEFAULT_UPLOAD_TIMEOUT_SECONDS)
    result_still_count = _env_int("GEMINI_RESULT_STILL_COUNT", DEFAULT_RESULT_STILL_COUNT)
    file_poll_interval = _env_float("GEMINI_FILE_POLL_INTERVAL_SECONDS", DEFAULT_FILE_POLL_INTERVAL_SECONDS)
    max_retries = _env_int("GEMINI_MAX_RETRIES", DEFAULT_GEMINI_MAX_RETRIES)
    retry_delay = _env_int("GEMINI_RETRY_DELAY_SECONDS", DEFAULT_GEMINI_RETRY_DELAY_SECONDS)

    uploaded_files = []

    with tempfile.TemporaryDirectory(prefix="smash_gemini_") as temp_dir:
        slowed_video_path = os.path.join(temp_dir, "result_screen_slowed.mp4")

        _log(logger, "info", f"Slowing result screen video by {slowdown_factor}x for Gemini.")
        _slow_down_video(result_video_path, slowed_video_path, slowdown_factor, output_fps)

        result_still_paths = _extract_result_stills(
            result_video_path,
            temp_dir,
            max_stills=result_still_count,
        )
        _log(logger, "info", f"Extracted {len(result_still_paths)} high-resolution result still(s) for OCR.")

        try:
            parts = []

            if context_image_path and os.path.exists(context_image_path):
                _log(logger, "info", "Uploading frame 42 image to Gemini for player-name context.")
                context_image_file = _upload_and_wait(
                    client,
                    context_image_path,
                    timeout_seconds,
                    file_poll_interval,
                    logger,
                )
                uploaded_files.append(context_image_file)
                parts.append(_make_file_part(context_image_file))

            for still_path in result_still_paths:
                still_file = _upload_and_wait(
                    client,
                    still_path,
                    timeout_seconds,
                    file_poll_interval,
                    logger,
                )
                uploaded_files.append(still_file)
                parts.append(_make_file_part(still_file))

            _log(logger, "info", "Uploading slowed result screen video to Gemini.")
            video_file = _upload_and_wait(
                client,
                slowed_video_path,
                timeout_seconds,
                file_poll_interval,
                logger,
            )
            uploaded_files.append(video_file)
            parts.append(_make_file_part(video_file, video_sample_fps=video_sample_fps))

            parts.append(
                types.Part.from_text(
                    text=_build_prompt(
                        has_player_context_image=bool(context_image_path and os.path.exists(context_image_path)),
                        result_still_count=len(result_still_paths),
                        player_name_examples=player_name_examples,
                    )
                )
            )

            models_to_try = _ordered_models(model)
            response = None
            last_rate_limit_error = None
            for retry_attempt in range(max_retries + 1):
                for candidate_model in models_to_try:
                    _log(
                        logger,
                        "info",
                        f"Analyzing result screen with {candidate_model}, "
                        f"media_resolution=HIGH, video_sample_fps={video_sample_fps}.",
                    )
                    try:
                        response = _generate_content(
                            client,
                            model=candidate_model,
                            config=_build_generate_config(),
                            contents=types.Content(role="user", parts=parts),
                        )
                        if candidate_model != model:
                            _log(logger, "info", f"Gemini fallback model succeeded: {candidate_model}")
                        break
                    except Exception as exc:
                        if not _is_rate_limit_error(exc):
                            raise
                        last_rate_limit_error = exc
                        _log(
                            logger,
                            "warning",
                            f"Gemini model {candidate_model} is rate limited. "
                            f"Details: {_exception_details(exc)}",
                        )

                if response is not None:
                    break

                if retry_attempt >= max_retries:
                    _log(
                        logger,
                        "error",
                        f"All Gemini models were rate limited after {retry_attempt + 1} full pass(es). "
                        f"Details: {_exception_details(last_rate_limit_error)}",
                    )
                    raise last_rate_limit_error

                _log(
                    logger,
                    "warning",
                    f"All Gemini models were rate limited; waiting {retry_delay}s before retrying "
                    f"the full model chain (retry {retry_attempt + 1}/{max_retries}).",
                )
                time.sleep(retry_delay)

            if response is None:
                raise RuntimeError("Gemini did not return a response from any configured model")

            match_stats = _parse_response(response)
            player_stats = _to_downstream_player_stats(match_stats)
            _log(logger, "info", f"Successfully extracted stats for {len(player_stats)} player(s).")
            return player_stats
        finally:
            _delete_uploaded_files(client, uploaded_files, logger)
