# 🚀 Windows Production Deployment Checklist

Use this checklist when deploying to your Windows production server.

---

## 📋 Pre-Deployment (On Mac)

- [x] ✅ YouTube OAuth authentication working
- [x] ✅ Test videos uploaded successfully
- [x] ✅ Code committed to GitHub
- [ ] 🔄 Push all changes to GitHub: `git push origin main`

---

## 📦 Files to Transfer to Windows

### ✅ Essential Files (Transfer Securely!)

Copy these files to USB drive or secure transfer:

```
📁 Credentials (KEEP SECRET!)
├── .env                                    ← Database & API keys
├── client_secrets.json                     ← YouTube OAuth client
└── youtube-upload-credentials.pickle       ← Saved auth tokens (optional but saves re-auth)
```

### 📥 Files Available from GitHub

These will be pulled from git on Windows:

```
📁 Code & Documentation
├── capture_card_processor.py
├── youtube_uploader.py
├── bulk_upload_to_youtube.py
├── requirements.txt
├── all other .py files
└── all .md documentation files
```

---

## 🪟 Windows Server Setup Steps

### 1. Install Python

- [ ] Download Python 3.10+ from https://www.python.org/downloads/
- [ ] During install: ✅ Check "Add Python to PATH"
- [ ] Verify: Open PowerShell and run `python --version`

### 2. Install UV (Optional but Recommended)

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

- [ ] Close and reopen PowerShell
- [ ] Verify: `uv --version`

### 3. Clone Repository

```powershell
cd C:\
git clone https://github.com/anishthite/smash-leaderboard-ai.git
cd smash-leaderboard-ai
```

Or if git not installed, download ZIP:
- [ ] Go to: https://github.com/anishthite/smash-leaderboard-ai
- [ ] Click "Code" → "Download ZIP"
- [ ] Extract to `C:\smash-leaderboard-ai`

### 4. Transfer Credentials

- [ ] Copy `.env` to `C:\smash-leaderboard-ai\.env`
- [ ] Copy `client_secrets.json` to `C:\smash-leaderboard-ai\client_secrets.json`
- [ ] Copy `youtube-upload-credentials.pickle` to `C:\smash-leaderboard-ai\youtube-upload-credentials.pickle` (optional)

### 5. Install Dependencies

```powershell
cd C:\smash-leaderboard-ai

# With UV (faster)
uv venv
uv pip install -r requirements.txt

# Or with standard Python
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 6. Verify Setup

```powershell
# Check OAuth setup
uv run python verify_oauth_setup.py

# Create test videos
uv run python create_test_videos.py
```

### 7. Test YouTube Upload

```powershell
# Dry run (no actual upload)
uv run python bulk_upload_to_youtube.py --directory matches\test_uploads --dry-run

# Real test upload
uv run python bulk_upload_to_youtube.py --directory matches\test_uploads
```

**Note**: If you transferred `youtube-upload-credentials.pickle`, it should work without browser authentication!

### 8. Test Capture Processor

```powershell
# Test with video file
uv run python capture_card_processor.py --test-mode --test-video-path path\to\test.mp4

# Live mode (requires capture card)
uv run python capture_card_processor.py
```

---

## 🔄 Configure as Windows Service

### Option A: Using NSSM (Recommended)

```powershell
# Download NSSM from https://nssm.cc/download
# Extract to C:\nssm

# Install service
C:\nssm\win64\nssm.exe install SmashLeaderboard ^
  "C:\smash-leaderboard-ai\.venv\Scripts\python.exe" ^
  "C:\smash-leaderboard-ai\capture_card_processor.py"

# Configure
C:\nssm\win64\nssm.exe set SmashLeaderboard AppDirectory "C:\smash-leaderboard-ai"
C:\nssm\win64\nssm.exe set SmashLeaderboard DisplayName "Smash Bros Leaderboard"
C:\nssm\win64\nssm.exe set SmashLeaderboard Start SERVICE_AUTO_START

# Set up logging
mkdir C:\smash-leaderboard-ai\logs
C:\nssm\win64\nssm.exe set SmashLeaderboard AppStdout "C:\smash-leaderboard-ai\logs\output.log"
C:\nssm\win64\nssm.exe set SmashLeaderboard AppStderr "C:\smash-leaderboard-ai\logs\error.log"

# Start service
C:\nssm\win64\nssm.exe start SmashLeaderboard
```

- [ ] Service installed
- [ ] Service started successfully
- [ ] Logs directory created

### Option B: Using Task Scheduler

See `WINDOWS_SETUP.md` for detailed Task Scheduler instructions.

---

## ✅ Post-Deployment Verification

### Test Everything Works

- [ ] Capture card detected: `uv run python test_devices.py`
- [ ] Database connection works (check `.env` credentials)
- [ ] YouTube uploads work (test with test videos)
- [ ] Service starts on boot (restart server and check)
- [ ] Logs are being written to `logs/` directory
- [ ] Matches auto-upload to YouTube after being saved

### Monitor for Issues

```powershell
# Check service status (NSSM)
C:\nssm\win64\nssm.exe status SmashLeaderboard

# View logs
Get-Content C:\smash-leaderboard-ai\logs\output.log -Tail 50 -Wait

# Check Windows Event Viewer
eventvwr.msc
```

---

## 📊 Production Operations

### Check Rankings

```powershell
uv run python elo_manager.py --rankings
```

### Bulk Upload Existing Videos

```powershell
# Upload all videos, skip already uploaded
uv run python bulk_upload_to_youtube.py --skip-uploaded

# Upload from specific directory
uv run python bulk_upload_to_youtube.py --directory matches\forlater
```

### Restart Service

```powershell
# NSSM
C:\nssm\win64\nssm.exe restart SmashLeaderboard

# Task Scheduler
Restart-ScheduledTask -TaskName "Smash Leaderboard"
```

### Update Code from GitHub

```powershell
cd C:\smash-leaderboard-ai

# Stop service
C:\nssm\win64\nssm.exe stop SmashLeaderboard

# Pull latest code
git pull origin main

# Reinstall dependencies if requirements changed
uv pip install -r requirements.txt

# Start service
C:\nssm\win64\nssm.exe start SmashLeaderboard
```

---

## 🔒 Security Checklist

- [ ] `.env` file permissions restricted (not world-readable)
- [ ] `client_secrets.json` not committed to git
- [ ] `youtube-upload-credentials.pickle` not committed to git
- [ ] Firewall configured to allow Python and capture card
- [ ] Windows Defender allows Python scripts
- [ ] Service runs with appropriate user permissions

---

## 🆘 Troubleshooting

### Issue: Python not found

```powershell
# Add to PATH temporarily
$env:Path += ";C:\Python310;C:\Python310\Scripts"
```

### Issue: Capture card not detected

```powershell
uv run python test_devices.py
# Try different device indexes
```

### Issue: YouTube upload fails with 403

- Check `youtube-upload-credentials.pickle` was transferred
- If not, will need to re-authenticate (browser will open)
- Make sure Gmail is added as test user in OAuth consent screen

### Issue: Service won't start

- Check logs in `C:\smash-leaderboard-ai\logs\`
- Verify Python path in NSSM configuration
- Try running manually first to see error

### Issue: Dependencies won't install

```powershell
# Try upgrading pip first
python -m pip install --upgrade pip

# Then install dependencies
pip install -r requirements.txt
```

---

## 📚 Documentation References

- `WINDOWS_SETUP.md` - Complete Windows setup guide
- `YOUTUBE_SETUP.md` - YouTube OAuth setup (if re-auth needed)
- `UV_SETUP.md` - UV package manager guide
- `FIXING_OAUTH_403.md` - OAuth troubleshooting

---

## 🎉 Deployment Complete!

Once all checkboxes are ticked:

- ✅ Service running on Windows
- ✅ Matches recording automatically
- ✅ Videos uploading to YouTube
- ✅ System running in production

Monitor the logs for the first few hours to ensure everything works smoothly!

---

## 📞 Quick Commands Reference

```powershell
# Check service status
C:\nssm\win64\nssm.exe status SmashLeaderboard

# View live logs
Get-Content C:\smash-leaderboard-ai\logs\output.log -Tail 50 -Wait

# Restart service
C:\nssm\win64\nssm.exe restart SmashLeaderboard

# Update from git
git pull origin main && uv pip install -r requirements.txt

# Bulk upload videos
uv run python bulk_upload_to_youtube.py --skip-uploaded
```

Good luck with deployment! 🚀
