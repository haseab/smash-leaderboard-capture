# 🎉 YouTube Upload Integration - Ready for Windows Deployment

## ✅ What We Built

Complete YouTube upload integration for Smash Bros Leaderboard with automatic uploads, bulk processing, and Windows production support.

---

## 🚀 Deployment Status

- ✅ **Code committed to GitHub**: Commit `7ccb4f5`
- ✅ **Tested on Mac**: OAuth authentication and uploads working
- ✅ **UV environment configured**: Fast Python package management
- ⏭️ **Ready for Windows deployment**

---

## 📦 What's in This Release

### Core Features

1. **Automatic YouTube Upload**
   - Videos upload to YouTube after matches are saved to database
   - Smart titles: "Player1 (Character1) vs Player2 (Character2) - Match #42"
   - Detailed descriptions with player stats
   - Automatic playlist creation

2. **Bulk Upload Script**
   - Upload existing videos from `matches/` and `matches/forlater/`
   - Matches legacy videos to database by timestamp
   - Progress bar and summary statistics
   - Skip already-uploaded videos

3. **Testing & Utilities**
   - Generate dummy test videos
   - Create test database records
   - OAuth verification diagnostic tool

### Documentation

- **YOUTUBE_SETUP.md** - Complete YouTube OAuth setup guide
- **YOUTUBE_QUICK_REFERENCE.md** - Quick command reference
- **TESTING_YOUTUBE_UPLOAD.md** - Comprehensive testing guide
- **FIXING_OAUTH_403.md** - OAuth 403 error troubleshooting
- **UV_SETUP.md** - UV package manager guide
- **UV_QUICK_START.md** - Quick start with UV
- **WINDOWS_SETUP.md** - Windows production deployment
- **DEPLOYMENT_CHECKLIST.md** - Step-by-step deployment guide

---

## 🪟 Deploying to Windows

### Quick Steps

1. **On Windows server**, clone the repo:
   ```powershell
   git clone https://github.com/anishthite/smash-leaderboard-ai.git
   cd smash-leaderboard-ai
   ```

2. **Transfer credentials** (USB or secure method):
   - `.env` (database credentials)
   - `client_secrets.json` (YouTube OAuth)
   - `youtube-upload-credentials.pickle` (saved auth tokens - optional)

3. **Install dependencies**:
   ```powershell
   uv venv
   uv pip install -r requirements.txt
   ```

4. **Test**:
   ```powershell
   uv run python create_test_videos.py
   uv run python bulk_upload_to_youtube.py --directory matches\test_uploads --dry-run
   ```

5. **Configure as Windows service** (see WINDOWS_SETUP.md)

**Full guide**: See `DEPLOYMENT_CHECKLIST.md` for complete step-by-step instructions

---

## 📋 Files to Transfer Securely

**⚠️ NEVER commit these files to git!**

```
.env                                 ← Database & API credentials
client_secrets.json                  ← YouTube OAuth client ID
youtube-upload-credentials.pickle    ← Saved auth tokens (optional)
```

All other files are in git and will be pulled on Windows.

---

## 🎮 How It Works

### Automatic Upload Flow

```
Match Played → Recorded → Saved to Database → Files Renamed
    → Metadata Added → Upload to YouTube → URL Saved to Database
```

### Video Format on YouTube

**Title**: "Anish (Kirby) vs John (Mario) - Match #42"

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

**Settings**: Public, Gaming category, Auto-added to "Smash Bros Matches" playlist

---

## 📊 Database Changes Required

Before first use, add YouTube URL column:

```sql
ALTER TABLE matches ADD COLUMN youtube_url TEXT;
```

This stores YouTube URLs for easy access later.

---

## 💡 Key Features

- ✅ **No manual intervention** - Fully automatic uploads
- ✅ **Smart matching** - Legacy videos matched to database by timestamp
- ✅ **Error resilient** - Upload failures don't crash the capture system
- ✅ **Quota aware** - Tracks daily upload limits (~6 videos/day)
- ✅ **Resumable uploads** - Handles network interruptions
- ✅ **Fast setup** - UV package manager for 10-100x faster installs
- ✅ **Windows ready** - Full Windows service integration

---

## 🎯 Quick Commands

### Mac (Development)

```bash
# Test upload
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run

# Run capture processor
uv run python capture_card_processor.py
```

### Windows (Production)

```powershell
# Test upload
uv run python bulk_upload_to_youtube.py --directory matches\test_uploads --dry-run

# Run as service
C:\nssm\win64\nssm.exe start SmashLeaderboard

# View logs
Get-Content C:\smash-leaderboard-ai\logs\output.log -Tail 50 -Wait
```

---

## ⚠️ Important Notes

### YouTube Quotas

- **Daily limit**: ~6 video uploads (10,000 quota units)
- **Resets**: Midnight Pacific Time
- **On quota exceeded**: Uploads fail gracefully, videos stay local
- **Solution**: Request quota increase from Google or spread uploads across days

### Authentication

- First run opens browser for OAuth
- Credentials cached in `youtube-upload-credentials.pickle`
- Transfer this file to Windows to avoid re-authentication
- Tokens refresh automatically (valid for ~6 months)

### File Naming

Videos must follow these formats:
- `{match_id}-{timestamp}.mp4` (e.g., `42-20240115_143052.mp4`)
- `{timestamp}.mp4` (legacy, e.g., `20240115_143052.mp4`)

Result screen videos are automatically excluded from uploads.

---

## 📚 Need Help?

**Setup Questions**: See `YOUTUBE_SETUP.md` or `WINDOWS_SETUP.md`

**OAuth Issues**: See `FIXING_OAUTH_403.md`

**Testing**: See `TESTING_YOUTUBE_UPLOAD.md`

**UV Questions**: See `UV_SETUP.md`

**Deployment**: See `DEPLOYMENT_CHECKLIST.md`

---

## 🎉 You're Ready!

Everything is committed and ready for Windows deployment. Just follow the deployment checklist and you'll be uploading matches to YouTube automatically!

**Repository**: https://github.com/anishthite/smash-leaderboard-ai

**Commit**: `7ccb4f5a8ebeaf6fbb2d9c84a4b2aef67a552019`

Good luck with deployment! 🚀
