# 🪟 Windows Production Server Setup Guide

## 📦 Prerequisites

- Windows 10/11 or Windows Server
- Python 3.10 or higher
- Administrator access (for initial setup)
- Internet connection

---

## 🚀 Quick Setup (Recommended)

### Option 1: Using UV (Fastest)

```powershell
# 1. Install UV
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Restart PowerShell to refresh PATH

# 3. Navigate to project directory
cd C:\path\to\smash-leaderboard-ai

# 4. Create virtual environment
uv venv

# 5. Install dependencies
uv pip install -r requirements.txt

# Done! ✅
```

### Option 2: Using Standard Python

```powershell
# 1. Create virtual environment
python -m venv .venv

# 2. Activate virtual environment
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
```

---

## 📁 Files to Transfer to Windows Server

### Essential Files

```
smash-leaderboard-ai/
├── capture_card_processor.py       ✅ Main application
├── youtube_uploader.py             ✅ YouTube upload module
├── bulk_upload_to_youtube.py       ✅ Bulk upload script
├── elo_utils.py                    ✅ ELO calculation
├── elo_manager.py                  ✅ ELO management
├── process_result_video.py         ✅ Manual video processing
├── requirements.txt                ✅ Dependencies
├── .env                            ✅ Environment variables (IMPORTANT!)
├── client_secrets.json             ✅ YouTube OAuth credentials
└── youtube-upload-credentials.pickle ✅ Saved auth tokens
```

### Documentation (Optional)

```
├── YOUTUBE_SETUP.md
├── YOUTUBE_QUICK_REFERENCE.md
├── WINDOWS_SETUP.md               ← This file
├── UV_SETUP.md
└── README files...
```

### Test Files (Optional)

```
├── create_test_videos.py
├── create_test_database_records.py
├── verify_oauth_setup.py
└── matches/test_uploads/          (test videos)
```

---

## 🔐 Transfer Credentials Securely

### Important Files to Transfer

1. **`.env`** - Database credentials
   ```
   SUPABASE_URL=your_url
   SUPABASE_SERVICE_ROLE_KEY=your_key
   GEMINI_API_KEY=your_key
   ```

2. **`client_secrets.json`** - YouTube OAuth client credentials

3. **`youtube-upload-credentials.pickle`** - Already authenticated tokens (so you don't need to authenticate again on Windows)

### Transfer Methods

**Method 1: Secure Copy (if both on same network)**
```bash
# From Mac to Windows (using SCP if Windows has SSH)
scp .env client_secrets.json youtube-upload-credentials.pickle user@windows-server:C:\path\to\project\
```

**Method 2: USB Drive**
- Copy files to encrypted USB
- Transfer to Windows server
- Delete from USB after

**Method 3: Secure Cloud Storage**
- Upload to password-protected cloud storage
- Download on Windows
- Delete from cloud after

**⚠️ NEVER commit these files to git or send via unencrypted email!**

---

## 🔧 Windows-Specific Setup

### 1. Install Python (if not installed)

Download from: https://www.python.org/downloads/

**Important**: During installation, check ✅ **"Add Python to PATH"**

Verify:
```powershell
python --version
# Should show: Python 3.10.x or higher
```

### 2. Install UV (Optional but Recommended)

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart PowerShell and verify:
```powershell
uv --version
```

### 3. Create Project Directory

```powershell
# Create directory
mkdir C:\SmashLeaderboard
cd C:\SmashLeaderboard

# Or use your preferred location
```

### 4. Transfer Files

Copy all files from Mac to Windows server (see "Transfer Credentials Securely" above)

### 5. Install Dependencies

**With UV (recommended):**
```powershell
uv venv
uv pip install -r requirements.txt
```

**Without UV:**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 🎮 Running on Windows

### Using UV (No activation needed)

```powershell
# Run capture processor
uv run python capture_card_processor.py

# Bulk upload videos
uv run python bulk_upload_to_youtube.py --skip-uploaded

# Check rankings
uv run python elo_manager.py --rankings
```

### Using Standard Python

```powershell
# Activate virtual environment
.venv\Scripts\activate

# Run capture processor
python capture_card_processor.py

# Bulk upload videos
python bulk_upload_to_youtube.py --skip-uploaded

# Check rankings
python elo_manager.py --rankings
```

---

## 🎬 Testing on Windows

### 1. Verify Setup

```powershell
# Check Python version
python --version

# Verify dependencies installed
uv pip list
# or
pip list
```

### 2. Create Test Videos (Optional)

```powershell
uv run python create_test_videos.py
```

### 3. Test YouTube Upload (Dry Run)

```powershell
uv run python bulk_upload_to_youtube.py --directory matches\test_uploads --dry-run
```

**Note**: If you transferred `youtube-upload-credentials.pickle` from Mac, it should work without re-authenticating!

### 4. Test Actual Upload

```powershell
uv run python bulk_upload_to_youtube.py --directory matches\test_uploads
```

### 5. Run Capture Processor

```powershell
# Test mode with video file
uv run python capture_card_processor.py --test-mode --test-video-path path\to\test.mp4

# Live mode (requires capture card)
uv run python capture_card_processor.py
```

---

## 🔄 Running as Windows Service (Production)

### Option 1: Using NSSM (Recommended)

NSSM (Non-Sucking Service Manager) makes it easy to run Python scripts as Windows services.

#### Install NSSM

1. Download from: https://nssm.cc/download
2. Extract to `C:\nssm`
3. Add to PATH or use full path

#### Create Service

```powershell
# Navigate to nssm directory
cd C:\nssm\win64

# Install service
.\nssm.exe install SmashLeaderboard "C:\SmashLeaderboard\.venv\Scripts\python.exe" "C:\SmashLeaderboard\capture_card_processor.py"

# Configure service
.\nssm.exe set SmashLeaderboard AppDirectory "C:\SmashLeaderboard"
.\nssm.exe set SmashLeaderboard DisplayName "Smash Bros Leaderboard"
.\nssm.exe set SmashLeaderboard Description "Automated Smash Bros match recording and YouTube upload"
.\nssm.exe set SmashLeaderboard Start SERVICE_AUTO_START

# Start service
.\nssm.exe start SmashLeaderboard
```

#### Manage Service

```powershell
# Check status
.\nssm.exe status SmashLeaderboard

# Stop service
.\nssm.exe stop SmashLeaderboard

# Restart service
.\nssm.exe restart SmashLeaderboard

# Remove service
.\nssm.exe remove SmashLeaderboard confirm
```

### Option 2: Using Task Scheduler

1. Open **Task Scheduler**
2. Create Task → **"Create Task..."**
3. **General tab**:
   - Name: `Smash Leaderboard`
   - Run whether user is logged on or not: ✅
   - Run with highest privileges: ✅
4. **Triggers tab**:
   - New → Begin: **At startup**
5. **Actions tab**:
   - New → Action: **Start a program**
   - Program: `C:\SmashLeaderboard\.venv\Scripts\python.exe`
   - Arguments: `capture_card_processor.py`
   - Start in: `C:\SmashLeaderboard`
6. **Conditions** & **Settings**: Adjust as needed
7. Click **OK**

---

## 📊 Monitoring and Logs

### View Logs

```powershell
# If using NSSM service
Get-Content C:\SmashLeaderboard\logs\app.log -Tail 50 -Wait

# Or check Windows Event Viewer
eventvwr.msc
```

### Create Log Directory

```powershell
mkdir C:\SmashLeaderboard\logs
```

### Redirect Output to Log File

Modify service to log output:

```powershell
# With NSSM
.\nssm.exe set SmashLeaderboard AppStdout "C:\SmashLeaderboard\logs\output.log"
.\nssm.exe set SmashLeaderboard AppStderr "C:\SmashLeaderboard\logs\error.log"
```

---

## 🔧 Windows-Specific Troubleshooting

### Issue: "python not recognized"

**Solution**: Add Python to PATH
```powershell
# Temporary (current session)
$env:Path += ";C:\Python310;C:\Python310\Scripts"

# Permanent: Add via System Environment Variables
# Settings → System → About → Advanced system settings → Environment Variables
```

### Issue: "Access denied" when running script

**Solution**: Run PowerShell as Administrator

### Issue: Capture card not detected

**Solution**: Check device index
```powershell
uv run python test_devices.py
```

### Issue: ffmpeg not found

**Solution**: Install ffmpeg
1. Download from: https://ffmpeg.org/download.html#build-windows
2. Extract to `C:\ffmpeg`
3. Add `C:\ffmpeg\bin` to PATH

### Issue: Virtual environment activation fails

**Solution**: Change execution policy
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Issue: YouTube authentication opens browser but no response

**Solution**: 
- Check firewall allows localhost connections
- Try running as administrator
- Manually copy URL from terminal to browser

---

## 🎯 Production Checklist

Before going live on Windows server:

- [ ] Python 3.10+ installed
- [ ] All files transferred securely
- [ ] `.env` file with correct credentials
- [ ] `client_secrets.json` in project root
- [ ] `youtube-upload-credentials.pickle` transferred (optional but saves re-auth)
- [ ] Dependencies installed
- [ ] Capture card connected and detected
- [ ] Test video upload works
- [ ] Service configured (NSSM or Task Scheduler)
- [ ] Logs directory created
- [ ] Firewall allows Python/capture card access

---

## 🚀 Quick Start Commands (Windows)

```powershell
# Setup
cd C:\SmashLeaderboard
uv venv
uv pip install -r requirements.txt

# Test
uv run python verify_oauth_setup.py
uv run python bulk_upload_to_youtube.py --directory matches\test_uploads --dry-run

# Run
uv run python capture_card_processor.py

# Service (NSSM)
nssm install SmashLeaderboard
nssm start SmashLeaderboard
```

---

## 📚 Additional Resources

- **NSSM Documentation**: https://nssm.cc/usage
- **Python Windows Setup**: https://docs.python.org/3/using/windows.html
- **Task Scheduler Guide**: https://docs.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page

---

## 🎉 You're Ready!

Your Smash Bros Leaderboard should now be running on Windows, automatically recording matches and uploading to YouTube!

For issues, check:
- Windows Event Viewer
- Log files in `logs/` directory
- NSSM service status

Good luck! 🚀
