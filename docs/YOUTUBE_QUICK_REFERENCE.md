# YouTube Upload - Quick Reference

## 🚀 Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Complete Google Cloud setup** (see YOUTUBE_SETUP.md for detailed steps)
   - Create Google Cloud project
   - Enable YouTube Data API v3
   - Create OAuth credentials
   - Download `client_secrets.json` to project root

3. **Add database column:**
   ```sql
   ALTER TABLE matches ADD COLUMN youtube_url TEXT;
   ```

4. **First-time authentication:**
   ```bash
   python bulk_upload_to_youtube.py --dry-run
   ```
   This will open your browser to authorize the app.

## 📝 Common Commands

### Bulk Upload Scripts

```bash
# Preview what would be uploaded
python bulk_upload_to_youtube.py --dry-run

# Upload all videos, skip already uploaded
python bulk_upload_to_youtube.py --skip-uploaded

# Upload only from specific directory
python bulk_upload_to_youtube.py --directory matches/forlater

# Force re-upload all videos
python bulk_upload_to_youtube.py --force

# Upload without adding to playlist
python bulk_upload_to_youtube.py --no-playlist
```

### Automatic Upload

When you run the capture processor, videos will automatically upload to YouTube after being saved to the database:

```bash
python capture_card_processor.py
```

## 📊 Video Format

### Title (preferred):
```
Anish (Kirby) vs John (Mario) - Match #42
```

### Title (fallback):
```
Match #42 - 2024-01-15 14:30
```

### Description:
```
Super Smash Bros Ultimate Match

Match ID: 42
Date: January 15, 2024 2:30 PM

Players:
- Anish (Kirby) - 3 KOs, 2 Falls, 1 SD - WINNER
- John (Mario) - 2 KOs, 3 Falls, 0 SD

Recorded with automated capture system
```

### Settings:
- **Privacy**: Public
- **Category**: Gaming (20)
- **Playlist**: Smash Bros Matches (auto-created)
- **Tags**: super smash bros, smash ultimate, ssbu, gameplay, competitive, [players], [characters]

## ⚠️ Important Notes

### YouTube Quotas
- **Daily limit**: ~6 video uploads per day (10,000 quota units)
- **Resets**: Midnight Pacific Time
- **When exceeded**: Uploads fail, videos stay local, try again tomorrow

### Legacy Video Matching
- Files without match IDs (e.g., `20240115_143052.mp4`) are matched to database by timestamp
- Tolerance: ±5 seconds
- If no match found, uploads with generic metadata

### File Naming
The script processes these formats:
- `42-20240115_143052.mp4` → Match ID 42 (fetches metadata from database)
- `20240115_143052.mp4` → Legacy format (tries to match to database by timestamp)

Result screen videos (`*_result_screen.mp4`) are automatically excluded.

## 🔧 Troubleshooting

**"OAuth credentials file not found"**
→ Make sure `client_secrets.json` is in project root

**"Quota exceeded"**
→ Wait until tomorrow (midnight PT) or request quota increase from Google

**"SUPABASE_URL not found"**
→ Check your `.env` file has database credentials

**Videos don't appear in playlist**
→ Check YouTube Studio → Playlists. Playlist is auto-created on first upload.

**Browser doesn't open for auth**
→ Copy the URL from terminal and paste in browser manually

## 📁 Files Added

- `youtube_uploader.py` - Core upload functionality
- `bulk_upload_to_youtube.py` - Bulk upload script
- `YOUTUBE_SETUP.md` - Detailed setup guide
- `YOUTUBE_QUICK_REFERENCE.md` - This file
- `client_secrets.json` - **YOU CREATE THIS** (OAuth credentials from Google Cloud)
- `youtube-upload-credentials.pickle` - Auto-generated (saved tokens)

## 🔒 Security

**Never commit these files:**
- `client_secrets.json`
- `youtube-upload-credentials.pickle`
- `*-oauth2.json`

They're already in `.gitignore` ✅

## 📚 More Info

See `YOUTUBE_SETUP.md` for complete step-by-step setup instructions.
