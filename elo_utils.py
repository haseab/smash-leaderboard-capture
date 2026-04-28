#!/usr/bin/env python3
"""
Shared ELO utilities for Smash Bros Leaderboard

This module contains all ELO calculation functions.
"""

import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from supabase import Client


CHARACTER_NAME_NORMALIZATION = {
    "ENDERMAN": "STEVE",
    "STEVE": "STEVE",
    "ALEX": "STEVE",
    "ZOMBIE": "STEVE",
    "KING K ROOL": "KING K. ROOL",
    "KING K. ROOL": "KING K. ROOL",
    "ROSALINA": "ROSALINA & LUMA",
}


def normalize_character_name(character_name: str) -> str:
    """Normalize character aliases so the same character shares one ranking row."""
    normalized_name = (character_name or "").strip().upper()
    return CHARACTER_NAME_NORMALIZATION.get(normalized_name, normalized_name)


def _coerce_match_created_at(
    match_created_at: Optional[Union[datetime.datetime, str]],
) -> str:
    if match_created_at is None:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    if isinstance(match_created_at, datetime.datetime):
        if match_created_at.tzinfo is None:
            match_created_at = match_created_at.replace(
                tzinfo=datetime.timezone.utc
            )
        return match_created_at.isoformat()

    return str(match_created_at)


def upsert_character_rankings(
    rows: List[Dict[str, Any]], supabase_client: Client
) -> bool:
    """Upsert persisted character ranking rows keyed by (player, smash_character)."""
    if not rows:
        return True

    try:
        supabase_client.table("character_rankings").upsert(
            rows,
            on_conflict="player,smash_character",
        ).execute()
        return True
    except Exception as e:
        print(f"Error upserting character rankings: {e}")
        return False


def _player_sort_key(player_id: Any) -> Tuple[int, Any]:
    try:
        return (0, int(player_id))
    except (TypeError, ValueError):
        return (1, str(player_id))


def normalize_team_player_ids(player_one_id: Any, player_two_id: Any) -> Tuple[Any, Any]:
    """Return a stable teammate order for unique team ranking rows."""
    return tuple(sorted([player_one_id, player_two_id], key=_player_sort_key))


def _team_lookup_key(player_one_id: Any, player_two_id: Any) -> Tuple[str, str]:
    ordered_ids = normalize_team_player_ids(player_one_id, player_two_id)
    return str(ordered_ids[0]), str(ordered_ids[1])


def upsert_team_rankings(
    rows: List[Dict[str, Any]], supabase_client: Client
) -> bool:
    """Upsert persisted 2v2 team ranking rows keyed by (player_one, player_two)."""
    if not rows:
        return True

    try:
        supabase_client.table("team_rankings").upsert(
            rows,
            on_conflict="player_one,player_two",
        ).execute()
        return True
    except Exception as e:
        print(f"Error upserting team rankings: {e}")
        return False


def persist_match_participant_elo_diffs(
    rows: List[Dict[str, Any]], supabase_client: Client
) -> bool:
    """Persist participant-level Elo deltas for existing match_participants rows."""
    if not rows:
        return True

    id_rows: List[Dict[str, Any]] = []
    fallback_rows: List[Dict[str, Any]] = []

    for row in rows:
        if row.get("id") is not None:
            id_rows.append(row)
            continue

        if row.get("match_id") is not None and row.get("player") is not None:
            fallback_rows.append(row)

    try:
        batch_size = 500
        for start in range(0, len(id_rows), batch_size):
            batch = id_rows[start : start + batch_size]
            batch_ids = [int(row["id"]) for row in batch]
            existing_rows_response = (
                supabase_client.table("match_participants")
                .select(
                    "id, match_id, player, smash_character, is_cpu, total_kos, "
                    "total_falls, total_sds, has_won"
                )
                .in_("id", batch_ids)
                .execute()
            )
            existing_rows = existing_rows_response.data or []
            existing_rows_by_id = {
                int(existing_row["id"]): existing_row for existing_row in existing_rows
            }
            upsert_batch: List[Dict[str, Any]] = []

            for row in batch:
                participant_id = int(row["id"])
                existing_row = existing_rows_by_id.get(participant_id)

                if not existing_row:
                    raise RuntimeError(
                        f"Missing match_participants row for id={participant_id}"
                    )

                upsert_batch.append(
                    {
                        "id": participant_id,
                        "match_id": existing_row["match_id"],
                        "player": existing_row["player"],
                        "smash_character": existing_row["smash_character"],
                        "is_cpu": existing_row["is_cpu"],
                        "total_kos": existing_row["total_kos"],
                        "total_falls": existing_row["total_falls"],
                        "total_sds": existing_row["total_sds"],
                        "has_won": existing_row["has_won"],
                        "elo_diff": row.get("elo_diff"),
                    }
                )

            supabase_client.table("match_participants").upsert(
                upsert_batch,
                on_conflict="id",
            ).execute()

        for row in fallback_rows:
            supabase_client.table("match_participants").update(
                {"elo_diff": row.get("elo_diff")}
            ).eq("match_id", row["match_id"]).eq("player", row["player"]).execute()

        return True
    except Exception as e:
        print(f"Error persisting match participant Elo diffs: {e}")
        return False


def update_character_rankings_for_streaming(
    player_a_id: str,
    player_b_id: str,
    character_a: str,
    character_b: str,
    winner: str,
    player_a_match_stats: Dict[str, int],
    player_b_match_stats: Dict[str, int],
    supabase_client: Client,
    match_created_at: Optional[Union[datetime.datetime, str]] = None,
    k: int = 32,
) -> Optional[Tuple[int, int]]:
    """
    Persist memoized character ELO/stats for a single 1v1 match.

    Character rankings use the same ranked-player gating as overall player ELO:
    the match only affects character rows when both underlying players are ranked.
    """
    try:
        players_response = (
            supabase_client.table("players")
            .select("id, top_ten_played")
            .in_("id", [player_a_id, player_b_id])
            .execute()
        )
        players_data = {player["id"]: player for player in players_response.data}

        player_a_data = players_data.get(player_a_id)
        player_b_data = players_data.get(player_b_id)

        if (
            not player_a_data
            or not player_b_data
            or player_a_data.get("top_ten_played", 0) < 3
            or player_b_data.get("top_ten_played", 0) < 3
        ):
            return None

        normalized_character_a = normalize_character_name(character_a)
        normalized_character_b = normalize_character_name(character_b)

        existing_rows_response = (
            supabase_client.table("character_rankings")
            .select("*")
            .in_("player", [player_a_id, player_b_id])
            .in_(
                "smash_character",
                [normalized_character_a, normalized_character_b],
            )
            .execute()
        )
        existing_rows = {
            (row["player"], row["smash_character"]): row
            for row in existing_rows_response.data
        }

        existing_row_a = existing_rows.get((player_a_id, normalized_character_a), {})
        existing_row_b = existing_rows.get((player_b_id, normalized_character_b), {})

        rating_a = int(existing_row_a.get("elo", 1200))
        rating_b = int(existing_row_b.get("elo", 1200))
        new_rating_a, new_rating_b = update_elo(rating_a, rating_b, winner, k)
        match_created_at_iso = _coerce_match_created_at(match_created_at)

        winner_upper = winner.upper()
        row_a = {
            "player": player_a_id,
            "smash_character": normalized_character_a,
            "elo": new_rating_a,
            "total_wins": int(existing_row_a.get("total_wins", 0))
            + (1 if winner_upper == "A" else 0),
            "total_losses": int(existing_row_a.get("total_losses", 0))
            + (1 if winner_upper == "B" else 0),
            "total_kos": int(existing_row_a.get("total_kos", 0))
            + int(player_a_match_stats.get("kos", 0)),
            "total_falls": int(existing_row_a.get("total_falls", 0))
            + int(player_a_match_stats.get("falls", 0)),
            "total_sds": int(existing_row_a.get("total_sds", 0))
            + int(player_a_match_stats.get("sds", 0)),
            "current_win_streak": (
                int(existing_row_a.get("current_win_streak", 0)) + 1
                if winner_upper == "A"
                else 0
            ),
            "last_match_date": match_created_at_iso,
        }
        row_b = {
            "player": player_b_id,
            "smash_character": normalized_character_b,
            "elo": new_rating_b,
            "total_wins": int(existing_row_b.get("total_wins", 0))
            + (1 if winner_upper == "B" else 0),
            "total_losses": int(existing_row_b.get("total_losses", 0))
            + (1 if winner_upper == "A" else 0),
            "total_kos": int(existing_row_b.get("total_kos", 0))
            + int(player_b_match_stats.get("kos", 0)),
            "total_falls": int(existing_row_b.get("total_falls", 0))
            + int(player_b_match_stats.get("falls", 0)),
            "total_sds": int(existing_row_b.get("total_sds", 0))
            + int(player_b_match_stats.get("sds", 0)),
            "current_win_streak": (
                int(existing_row_b.get("current_win_streak", 0)) + 1
                if winner_upper == "B"
                else 0
            ),
            "last_match_date": match_created_at_iso,
        }

        if not upsert_character_rankings([row_a, row_b], supabase_client):
            return None

        return new_rating_a, new_rating_b

    except Exception as e:
        print(
            "Error updating character rankings for match "
            f"{player_a_id} vs {player_b_id}: {e}"
        )
        return None


def update_team_rankings_for_streaming(
    participants: List[Dict[str, Any]],
    supabase_client: Client,
    match_created_at: Optional[Union[datetime.datetime, str]] = None,
    k: int = 32,
) -> Optional[Dict[str, Any]]:
    """
    Persist memoized team ELO/stats for a single 2v2 match.

    Team rankings only update when the match has exactly two winners, exactly
    two losers, and all four underlying players are already ranked.
    """
    try:
        if len(participants) != 4:
            return None

        winners = [participant for participant in participants if participant.get("has_won")]
        losers = [participant for participant in participants if not participant.get("has_won")]

        if len(winners) != 2 or len(losers) != 2:
            return None

        player_ids = [participant["id"] for participant in participants]
        players_response = (
            supabase_client.table("players")
            .select("id, top_ten_played")
            .in_("id", player_ids)
            .execute()
        )
        players_data = {str(player["id"]): player for player in players_response.data}

        if any(
            str(player_id) not in players_data
            or players_data[str(player_id)].get("top_ten_played", 0) < 3
            for player_id in player_ids
        ):
            return None

        winning_team_ids = normalize_team_player_ids(
            winners[0]["id"], winners[1]["id"]
        )
        losing_team_ids = normalize_team_player_ids(losers[0]["id"], losers[1]["id"])
        possible_team_player_ids = list(
            {
                winning_team_ids[0],
                winning_team_ids[1],
                losing_team_ids[0],
                losing_team_ids[1],
            }
        )

        existing_rows_response = (
            supabase_client.table("team_rankings")
            .select("*")
            .in_("player_one", possible_team_player_ids)
            .in_("player_two", possible_team_player_ids)
            .execute()
        )
        existing_rows = {
            _team_lookup_key(row["player_one"], row["player_two"]): row
            for row in existing_rows_response.data
        }

        winning_key = _team_lookup_key(winning_team_ids[0], winning_team_ids[1])
        losing_key = _team_lookup_key(losing_team_ids[0], losing_team_ids[1])
        existing_winning_row = existing_rows.get(winning_key, {})
        existing_losing_row = existing_rows.get(losing_key, {})

        winning_rating = int(existing_winning_row.get("elo", 1200))
        losing_rating = int(existing_losing_row.get("elo", 1200))
        new_winning_rating, new_losing_rating = update_elo(
            winning_rating, losing_rating, "A", k
        )
        match_created_at_iso = _coerce_match_created_at(match_created_at)

        winning_totals = {
            "kos": sum(int(participant.get("kos", 0)) for participant in winners),
            "falls": sum(int(participant.get("falls", 0)) for participant in winners),
            "sds": sum(int(participant.get("sds", 0)) for participant in winners),
        }
        losing_totals = {
            "kos": sum(int(participant.get("kos", 0)) for participant in losers),
            "falls": sum(int(participant.get("falls", 0)) for participant in losers),
            "sds": sum(int(participant.get("sds", 0)) for participant in losers),
        }

        winning_row = {
            "player_one": winning_team_ids[0],
            "player_two": winning_team_ids[1],
            "elo": new_winning_rating,
            "total_wins": int(existing_winning_row.get("total_wins", 0)) + 1,
            "total_losses": int(existing_winning_row.get("total_losses", 0)),
            "total_kos": int(existing_winning_row.get("total_kos", 0))
            + winning_totals["kos"],
            "total_falls": int(existing_winning_row.get("total_falls", 0))
            + winning_totals["falls"],
            "total_sds": int(existing_winning_row.get("total_sds", 0))
            + winning_totals["sds"],
            "current_win_streak": int(
                existing_winning_row.get("current_win_streak", 0)
            )
            + 1,
            "last_match_date": match_created_at_iso,
        }
        losing_row = {
            "player_one": losing_team_ids[0],
            "player_two": losing_team_ids[1],
            "elo": new_losing_rating,
            "total_wins": int(existing_losing_row.get("total_wins", 0)),
            "total_losses": int(existing_losing_row.get("total_losses", 0)) + 1,
            "total_kos": int(existing_losing_row.get("total_kos", 0))
            + losing_totals["kos"],
            "total_falls": int(existing_losing_row.get("total_falls", 0))
            + losing_totals["falls"],
            "total_sds": int(existing_losing_row.get("total_sds", 0))
            + losing_totals["sds"],
            "current_win_streak": 0,
            "last_match_date": match_created_at_iso,
        }

        if not upsert_team_rankings([winning_row, losing_row], supabase_client):
            return None

        return {
            "winning_team": winning_row,
            "losing_team": losing_row,
            "old_winning_elo": winning_rating,
            "old_losing_elo": losing_rating,
        }

    except Exception as e:
        print(f"Error updating team rankings for 2v2 match: {e}")
        return None


def calculate_top_ten_played_for_player(player_id: str, supabase_client: Client) -> int:
    """
    Calculate how many original top 10 players this specific player has faced.
    Used for incremental updates after each match.
    
    Args:
        player_id: The player to calculate top_ten_played for
        supabase_client: Supabase client instance
    
    Returns:
        int: Number of original top 10 players this player has faced
    """
    try:
        # Get original top 10 player IDs (ranked players with highest ELO, excluding inactive)
        players_response = supabase_client.table("players").select("id, elo, top_ten_played, inactive").execute()
        players_data = players_response.data

        # Filter ranked players (top_ten_played >= 3) and active, then get top 10 by ELO
        ranked_players = [p for p in players_data if p.get('top_ten_played', 0) >= 3 and not p.get('inactive', False)]
        ranked_players.sort(key=lambda x: x['elo'], reverse=True)
        original_top_ten_ids = {p['id'] for p in ranked_players[:10]}
        
        if not original_top_ten_ids:
            return 0
        
        # Get all 1v1 matches this player participated in
        participants_response = supabase_client.table("match_participants")\
            .select("match_id")\
            .eq("player", player_id)\
            .execute()
        
        if not participants_response.data:
            return 0
        
        match_ids = [p['match_id'] for p in participants_response.data]
        
        # Get all participants for these matches to find opponents
        all_participants_response = supabase_client.table("match_participants")\
            .select("match_id, player")\
            .in_("match_id", match_ids)\
            .execute()
        
        # Group by match_id and find 1v1 matches where player faced top 10
        match_participants = {}
        for participant in all_participants_response.data:
            match_id = participant['match_id']
            if match_id not in match_participants:
                match_participants[match_id] = []
            match_participants[match_id].append(participant['player'])
        
        # Find unique top 10 opponents in 1v1 matches
        top_ten_opponents = set()
        for match_id, players in match_participants.items():
            if len(players) == 2 and player_id in players:
                # Find the opponent
                opponent_id = players[0] if players[1] == player_id else players[1]
                if opponent_id in original_top_ten_ids:
                    top_ten_opponents.add(opponent_id)
        
        return len(top_ten_opponents)
        
    except Exception as e:
        print(f"Error calculating top_ten_played for player {player_id}: {e}")
        return 0


def update_player_top_ten_played(player_id: str, top_ten_played: int, supabase_client: Client) -> bool:
    """
    Update a player's top_ten_played count in the database.
    
    Args:
        player_id: The player to update
        top_ten_played: New top_ten_played count
        supabase_client: Supabase client instance
    
    Returns:
        bool: True if update succeeded, False otherwise
    """
    try:
        supabase_client.table("players")\
            .update({"top_ten_played": top_ten_played})\
            .eq("id", player_id)\
            .execute()
        return True
    except Exception as e:
        print(f"Error updating top_ten_played for player {player_id}: {e}")
        return False


def check_if_player_becomes_ranked(player_id: str, supabase_client: Client) -> Tuple[bool, int]:
    """
    Check if a player becomes ranked (top_ten_played >= 3) after updating their top_ten_played.
    
    Args:
        player_id: The player to check
        supabase_client: Supabase client instance
    
    Returns:
        Tuple[bool, int]: (became_ranked, new_top_ten_played_count)
    """
    try:
        # Get player's current ranking status
        player_response = supabase_client.table("players")\
            .select("top_ten_played")\
            .eq("id", player_id)\
            .execute()
        
        if not player_response.data:
            return False, 0
        
        old_top_ten_played = player_response.data[0].get('top_ten_played', 0)
        was_ranked = old_top_ten_played >= 3
        
        # Calculate new top_ten_played count
        new_top_ten_played = calculate_top_ten_played_for_player(player_id, supabase_client)
        
        # Update the database with new count
        update_player_top_ten_played(player_id, new_top_ten_played, supabase_client)
        
        # Check if player became ranked
        is_now_ranked = new_top_ten_played >= 3
        became_ranked = not was_ranked and is_now_ranked
        
        return became_ranked, new_top_ten_played
        
    except Exception as e:
        print(f"Error checking if player {player_id} becomes ranked: {e}")
        return False, 0


def recalculate_all_matches_for_player(player_id: str, supabase_client: Client) -> bool:
    """
    Recalculate ELO for all historical matches involving a newly ranked player.
    This ensures their match history is properly processed with ELO updates.
    
    Args:
        player_id: The newly ranked player
        supabase_client: Supabase client instance
    
    Returns:
        bool: True if recalculation succeeded, False otherwise
    """
    try:
        print(f"Recalculating all matches for newly ranked player {player_id}")
        
        # Get all matches this player participated in, chronologically
        participants_response = supabase_client.table("match_participants")\
            .select("match_id")\
            .eq("player", player_id)\
            .execute()
        
        if not participants_response.data:
            return True
        
        match_ids = [p['match_id'] for p in participants_response.data]
        
        # Get match details with dates
        matches_response = supabase_client.table("matches")\
            .select("id, created_at")\
            .in_("id", match_ids)\
            .eq("archived", False)\
            .order("created_at", desc=False)\
            .execute()
        
        if not matches_response.data:
            return True
        
        # Get all current player data for ranking checks
        all_players_response = supabase_client.table("players").select("id, elo, top_ten_played").execute()
        players_data = {p['id']: p for p in all_players_response.data}
        
        processed_matches = 0
        elo_updates = 0
        
        for match in matches_response.data:
            match_id = match['id']
            
            # Get participants for this match
            match_participants_response = supabase_client.table("match_participants")\
                .select("player, has_won")\
                .eq("match_id", match_id)\
                .execute()
            
            participants = match_participants_response.data
            
            if len(participants) != 2:
                continue
            
            player_ids = [p['player'] for p in participants]
            winners = [p['player'] for p in participants if p['has_won']]
            
            if len(winners) != 1:
                continue
            
            # Check if both players are now ranked
            player1_id, player2_id = player_ids[0], player_ids[1]
            player1_data = players_data.get(player1_id)
            player2_data = players_data.get(player2_id)
            
            if (not player1_data or not player2_data or 
                player1_data.get('top_ten_played', 0) < 3 or 
                player2_data.get('top_ten_played', 0) < 3):
                continue
            
            # Get current ELOs
            rating_a = player1_data['elo']
            rating_b = player2_data['elo']
            winner_id = winners[0]
            winner = 'A' if winner_id == player1_id else 'B'
            
            # Calculate new ELOs using streaming function
            new_elo_a, new_elo_b = calculate_elo_update_for_streaming(
                rating_a, rating_b, winner, player1_id, player2_id, supabase_client
            )
            
            # Update ELOs in database if they changed
            if new_elo_a != rating_a:
                supabase_client.table("players")\
                    .update({"elo": new_elo_a})\
                    .eq("id", player1_id)\
                    .execute()
                players_data[player1_id]['elo'] = new_elo_a
                
            if new_elo_b != rating_b:
                supabase_client.table("players")\
                    .update({"elo": new_elo_b})\
                    .eq("id", player2_id)\
                    .execute()
                players_data[player2_id]['elo'] = new_elo_b
            
            if new_elo_a != rating_a or new_elo_b != rating_b:
                elo_updates += 1
            
            processed_matches += 1
        
        print(f"Processed {processed_matches} matches for player {player_id}, {elo_updates} ELO updates applied")
        return True
        
    except Exception as e:
        print(f"Error recalculating matches for player {player_id}: {e}")
        return False




def update_elo(rating_a: float, rating_b: float, winner: str, k: int = 32) -> tuple[int, int]:
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




def update_inactivity_status(supabase_client: Client, inactivity_threshold_weeks: int = 4) -> bool:
    """
    Update player inactivity status based on last match date.
    Players with no matches in the last N weeks are marked as inactive.
    
    Optimized to use a single SQL query instead of per-player queries.
    
    Args:
        supabase_client: Supabase client instance
        inactivity_threshold_weeks: Number of weeks of inactivity before marking as inactive (default: 4)
    
    Returns:
        bool: True if update succeeded, False otherwise
    """
    try:
        from datetime import timedelta
        
        # Use raw SQL query for efficiency - calculate everything in the database
        threshold_date = datetime.datetime.now(datetime.timezone.utc) - timedelta(weeks=inactivity_threshold_weeks)
        threshold_date_str = threshold_date.isoformat()
        
        # Execute raw SQL query to get players that need status updates
        query = f"""
        WITH player_last_match AS (
            SELECT DISTINCT ON (p.id)
                p.id,
                p.inactive as current_inactive,
                COALESCE(MAX(m.created_at), p.created_at) as last_activity
            FROM players p
            LEFT JOIN match_participants mp ON mp.player = p.id AND mp.is_cpu = false
            LEFT JOIN matches m ON m.id = mp.match_id AND m.archived = false
            GROUP BY p.id, p.inactive, p.created_at
        )
        SELECT 
            id,
            current_inactive,
            last_activity < '{threshold_date_str}'::timestamptz as should_be_inactive
        FROM player_last_match
        WHERE current_inactive != (last_activity < '{threshold_date_str}'::timestamptz)
        """
        
        try:
            # Execute using Supabase RPC
            result = supabase_client.rpc('exec_sql', {'query': query}).execute()
            updates_needed = result.data if result.data else []
        except Exception as e:
            # If RPC doesn't exist, fall back to the postgrest query syntax
            # Use a simpler approach with PostgREST
            print(f"RPC not available, using alternative approach: {e}")
            
            # Get player activity using PostgREST joins
            result = supabase_client.from_('players').select("""
                id,
                inactive,
                created_at,
                match_participants!left(
                    matches!inner(
                        created_at
                    )
                )
            """).execute()
            
            updates_needed = []
            now = datetime.datetime.now(datetime.timezone.utc)
            
            for player in result.data:
                player_id = player['id']
                current_inactive = player.get('inactive', False)
                created_at_str = player['created_at']
                
                # Parse creation date
                if isinstance(created_at_str, str):
                    created_at = datetime.datetime.fromisoformat(created_at_str.replace('Z', '+00:00') if created_at_str.endswith('Z') else created_at_str)
                else:
                    created_at = created_at_str
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=datetime.timezone.utc)
                
                # Find most recent match
                last_activity = created_at
                participants = player.get('match_participants', [])
                for participant in participants:
                    if participant and 'matches' in participant and participant['matches']:
                        match_date_str = participant['matches'].get('created_at')
                        if match_date_str:
                            match_date = datetime.datetime.fromisoformat(match_date_str.replace('Z', '+00:00') if match_date_str.endswith('Z') else match_date_str)
                            if match_date.tzinfo is None:
                                match_date = match_date.replace(tzinfo=datetime.timezone.utc)
                            if match_date > last_activity:
                                last_activity = match_date
                
                # Check if inactive status should change
                should_be_inactive = (now - last_activity).days >= (inactivity_threshold_weeks * 7)
                if should_be_inactive != current_inactive:
                    updates_needed.append({
                        'id': player_id,
                        'should_be_inactive': should_be_inactive
                    })
        
        # Apply updates
        updated_count = 0
        activated_count = 0
        deactivated_count = 0
        
        for update in updates_needed:
            try:
                should_be_inactive = update.get('should_be_inactive', False)
                supabase_client.table("players")\
                    .update({"inactive": should_be_inactive})\
                    .eq("id", update['id'])\
                    .execute()
                updated_count += 1
                if should_be_inactive:
                    deactivated_count += 1
                else:
                    activated_count += 1
            except Exception as e:
                print(f"Error updating player {update['id']}: {e}")
        
        if updated_count > 0:
            print(f"Inactivity status updated: {updated_count} players changed ({activated_count} activated, {deactivated_count} deactivated)")
        else:
            print("Inactivity status check complete: No changes needed")
        
        return True
        
    except Exception as e:
        print(f"Error updating inactivity status: {e}")
        import traceback
        traceback.print_exc()
        return False


def calculate_elo_update_for_streaming(
    rating_a: float,
    rating_b: float,
    winner: str,
    player_a_id: str,
    player_b_id: str,
    supabase_client: Client,
    k: int = 32,
    return_metadata: bool = False,
):
    """
    High-level function to calculate ELO update for streaming (real-time) processing.
    Only processes ELO updates if both players are ranked (top_ten_played >= 3).
    Also handles dynamic ranking progression for unranked players.
    
    Returns:
        tuple[int, int]: (new_elo_a, new_elo_b)
    """
    metadata = {"triggered_full_recompute": False}

    # Check if both players are ranked before processing ELO updates
    players_response = supabase_client.table("players").select("id, elo, top_ten_played").execute()
    players_data = {p['id']: p for p in players_response.data}
    
    player_a_data = players_data.get(player_a_id)
    player_b_data = players_data.get(player_b_id)
    
    # First, update top_ten_played for any unranked players and check if they become ranked
    newly_ranked_players = []
    
    if player_a_data and player_a_data.get('top_ten_played', 0) < 3:
        became_ranked, new_count = check_if_player_becomes_ranked(player_a_id, supabase_client)
        if became_ranked:
            newly_ranked_players.append(player_a_id)
            player_a_data['top_ten_played'] = new_count
            print(f"Player {player_a_id} became ranked! top_ten_played: {new_count}")
    
    if player_b_data and player_b_data.get('top_ten_played', 0) < 3:
        became_ranked, new_count = check_if_player_becomes_ranked(player_b_id, supabase_client)
        if became_ranked:
            newly_ranked_players.append(player_b_id)
            player_b_data['top_ten_played'] = new_count
            print(f"Player {player_b_id} became ranked! top_ten_played: {new_count}")
    
    # Trigger full batch recompute if any player became ranked
    # This is necessary because newly ranked players affect everyone's historical ELOs
    if newly_ranked_players:
        print(f"Triggering full ELO recompute due to newly ranked players: {newly_ranked_players}")
        metadata["triggered_full_recompute"] = True
        # Import and run the batch recompute function
        try:
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.abspath(__file__)))
            from recompute_all_player_elos import recompute_all_player_elos
            recompute_all_player_elos()

            refreshed_players_response = (
                supabase_client.table("players")
                .select("id, elo, top_ten_played")
                .in_("id", [player_a_id, player_b_id])
                .execute()
            )
            refreshed_players = {
                player["id"]: player for player in refreshed_players_response.data
            }

            refreshed_player_a = refreshed_players.get(player_a_id)
            refreshed_player_b = refreshed_players.get(player_b_id)

            if refreshed_player_a and refreshed_player_b:
                result = (
                    int(refreshed_player_a.get("elo", rating_a)),
                    int(refreshed_player_b.get("elo", rating_b)),
                )
                if return_metadata:
                    return result[0], result[1], metadata
                return result
        except Exception as e:
            print(f"Error during full recompute: {e}")
            # Fall back to individual recalculation if batch fails
            for player_id in newly_ranked_players:
                recalculate_all_matches_for_player(player_id, supabase_client)
    
    # Return original ELOs if either player is not found or not ranked
    if (not player_a_data or not player_b_data or 
        player_a_data.get('top_ten_played', 0) < 3 or 
        player_b_data.get('top_ten_played', 0) < 3):
        result = (int(rating_a), int(rating_b))
        if return_metadata:
            return result[0], result[1], metadata
        return result
    
    # Use normal ELO calculation
    new_elo_a, new_elo_b = update_elo(rating_a, rating_b, winner, k)
    if return_metadata:
        return new_elo_a, new_elo_b, metadata
    return new_elo_a, new_elo_b
