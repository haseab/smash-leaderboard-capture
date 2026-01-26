#!/usr/bin/env python3
"""
Create test database records for dummy match videos
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv()

def create_test_matches():
    """Create test match records in database"""
    
    # Initialize Supabase
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            print("❌ Error: SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not found in .env")
            sys.exit(1)
        
        supabase = create_client(supabase_url, supabase_key)
        print("✅ Connected to Supabase\n")
    except Exception as e:
        print(f"❌ Failed to initialize Supabase: {e}")
        sys.exit(1)
    
    # First, ensure test players exist
    test_players = [
        "Anish",
        "John",
        "Sarah",
        "Mike",
        "Emily",
        "David"
    ]
    
    print("Creating test players...")
    player_ids = {}
    
    for player_name in test_players:
        try:
            response = supabase.table("players").upsert(
                {"display_name": player_name},
                on_conflict="display_name"
            ).execute()
            
            if response.data:
                player_ids[player_name] = response.data[0]['id']
                print(f"  ✅ {player_name} (ID: {player_ids[player_name]})")
        except Exception as e:
            print(f"  ⚠️  Failed to create player {player_name}: {e}")
    
    print()
    
    # Define test matches
    test_matches = [
        {
            'id': 1,
            'created_at': '2024-01-15T14:30:52Z',
            'participants': [
                {'player': 'Anish', 'character': 'KIRBY', 'kos': 3, 'falls': 2, 'sds': 0, 'won': True},
                {'player': 'John', 'character': 'MARIO', 'kos': 2, 'falls': 3, 'sds': 1, 'won': False}
            ]
        },
        {
            'id': 2,
            'created_at': '2024-01-15T15:02:30Z',
            'participants': [
                {'player': 'Sarah', 'character': 'PIKACHU', 'kos': 4, 'falls': 1, 'sds': 0, 'won': True},
                {'player': 'Mike', 'character': 'LINK', 'kos': 1, 'falls': 4, 'sds': 2, 'won': False}
            ]
        },
        {
            'id': 3,
            'created_at': '2024-01-16T12:00:00Z',
            'participants': [
                {'player': 'Emily', 'character': 'SAMUS', 'kos': 2, 'falls': 2, 'sds': 1, 'won': False},
                {'player': 'David', 'character': 'FOX', 'kos': 3, 'falls': 2, 'sds': 0, 'won': True}
            ]
        }
    ]
    
    print("Creating test matches...")
    
    for match in test_matches:
        try:
            # Check if match already exists
            existing = supabase.table("matches").select("id").eq("id", match['id']).execute()
            
            if existing.data and len(existing.data) > 0:
                print(f"  ℹ️  Match {match['id']} already exists, skipping...")
                continue
            
            # Create match
            match_response = supabase.table("matches").insert({
                'id': match['id'],
                'created_at': match['created_at']
            }).execute()
            
            if not match_response.data:
                print(f"  ❌ Failed to create match {match['id']}")
                continue
            
            print(f"  ✅ Created Match #{match['id']} ({match['created_at']})")
            
            # Create participants
            for participant in match['participants']:
                player_name = participant['player']
                
                if player_name not in player_ids:
                    print(f"    ⚠️  Skipping participant {player_name} (player not found)")
                    continue
                
                participant_data = {
                    'match_id': match['id'],
                    'player': player_ids[player_name],
                    'smash_character': participant['character'],
                    'is_cpu': False,
                    'total_kos': participant['kos'],
                    'total_falls': participant['falls'],
                    'total_sds': participant['sds'],
                    'has_won': participant['won']
                }
                
                supabase.table("match_participants").insert(participant_data).execute()
                
                winner_tag = " 🏆" if participant['won'] else ""
                print(f"    - {player_name} ({participant['character']}) - "
                      f"{participant['kos']} KOs, {participant['falls']} Falls, "
                      f"{participant['sds']} SD{winner_tag}")
            
        except Exception as e:
            print(f"  ❌ Error creating match {match['id']}: {e}")
    
    print("\n" + "="*60)
    print("✅ Test database records created!")
    print("="*60)
    print("\nYou can now test the YouTube upload with:")
    print("  python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run")
    print("\nThe script will fetch player data from these database records.")
    print()


if __name__ == "__main__":
    create_test_matches()
