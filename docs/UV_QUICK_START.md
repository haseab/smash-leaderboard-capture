# 🎮 Smash Bros Leaderboard - UV Quick Start

## ✅ Setup Complete!

Your environment is ready to go with `uv`!

### 🚀 What's Already Done

- ✅ Virtual environment created (`.venv`)
- ✅ All dependencies installed with `uv`
- ✅ Test videos created in `matches/test_uploads/` and `matches/forlater/`
- ✅ YouTube upload integration ready

---

## 📋 Quick Commands

### Test Videos
```bash
# Create test videos (already done)
uv run python create_test_videos.py

# Create test database records
uv run python create_test_database_records.py
```

### YouTube Upload Testing
```bash
# Preview uploads (dry run)
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run

# Upload one test video
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads

# Upload all test videos
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads matches/forlater
```

### Main Application
```bash
# Run capture processor
uv run python capture_card_processor.py

# Check ELO rankings
uv run python elo_manager.py --rankings

# Process a video manually
uv run python process_result_video.py path/to/video.mp4
```

---

## 📚 Documentation

- **`UV_SETUP.md`** - Complete UV usage guide
- **`YOUTUBE_SETUP.md`** - YouTube OAuth setup (REQUIRED before uploading)
- **`YOUTUBE_QUICK_REFERENCE.md`** - YouTube command reference
- **`TESTING_YOUTUBE_UPLOAD.md`** - Detailed testing instructions

---

## 🎯 Next Steps

### 1. YouTube Setup (Required)

Before you can upload to YouTube, complete the setup in `YOUTUBE_SETUP.md`:

1. Create Google Cloud project
2. Enable YouTube Data API v3
3. Create OAuth credentials
4. Download `client_secrets.json` to project root
5. Run first authentication

### 2. Database Setup

Add YouTube URL column to your Supabase database:

```sql
ALTER TABLE matches ADD COLUMN youtube_url TEXT;
```

### 3. Test Upload

```bash
# This will open your browser for authentication
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
```

---

## 🎮 Test Videos Available

**matches/test_uploads/** (3 videos):
- `1-20240115_143052.mp4` - Match #1 (6.5 MB)
- `2-20240115_150230.mp4` - Match #2 (6.6 MB)
- `3-20240116_120000.mp4` - Match #3 (6.6 MB)

**matches/forlater/** (3 videos):
- `20240114_100000.mp4` - Legacy format (6.6 MB)
- `20240114_110000.mp4` - Legacy format (6.6 MB)
- `20240114_120000.mp4` - Legacy format (6.5 MB)

---

## 💡 Why UV?

UV is 10-100x faster than pip:
- ⚡ Parallel package installation
- 📦 Smart caching across projects
- 🔄 No need to activate venv (`uv run`)
- 🚀 Resolves dependencies in milliseconds

---

## 🔧 Common Commands

```bash
# Activate virtual environment (optional with uv run)
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Run any script without activating venv
uv run python script.py

# Add a new package
uv pip install package-name

# Update all dependencies
uv pip install --upgrade -r requirements.txt
```

---

## 🎬 How It Works

### Automatic Upload Flow

```
Match Recorded → Saved to Database → Renamed with Match ID
    → Metadata Added → Upload to YouTube → URL Saved to DB
```

### Video Format on YouTube

**Title**: `"Anish (Kirby) vs John (Mario) - Match #42"`

**Description**:
```
Super Smash Bros Ultimate Match

Match ID: 42
Date: January 15, 2024 2:30 PM

Players:
- Anish (Kirby) - 3 KOs, 2 Falls, 0 SD - WINNER
- John (Mario) - 2 KOs, 3 Falls, 1 SD

Recorded with automated capture system
```

**Settings**:
- Privacy: Public
- Category: Gaming
- Playlist: "Smash Bros Matches" (auto-created)

---

## ⚠️ Important Notes

**YouTube Quotas:**
- You can upload ~6 videos per day (10,000 quota units)
- Quota resets at midnight Pacific Time
- If exceeded, uploads fail gracefully (videos stay local)

**Credentials:**
- Never commit `client_secrets.json` or credential files
- Already in `.gitignore` ✅

---

## 🆘 Troubleshooting

**"uv: command not found"**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.cargo/bin:$PATH"
```

**"OAuth credentials file not found"**
→ Complete YouTube setup (see `YOUTUBE_SETUP.md`)

**"No module named 'X'"**
```bash
uv pip install -r requirements.txt
```

**Browser doesn't open for auth**
→ Copy URL from terminal and paste in browser

---

## 🎉 You're Ready!

Everything is set up and ready to go. Just complete the YouTube setup and start uploading!

For detailed instructions, see:
- `YOUTUBE_SETUP.md` - Step-by-step YouTube setup
- `TESTING_YOUTUBE_UPLOAD.md` - Testing guide
- `UV_SETUP.md` - Complete UV documentation

Have fun! 🚀
