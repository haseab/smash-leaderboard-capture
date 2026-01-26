# YouTube Upload Setup Guide

This guide will walk you through setting up automatic YouTube uploads for your Smash Bros Leaderboard matches.

## 🎯 Overview

After setup, your system will:
- **Automatically upload** match videos to YouTube after they're saved to the database
- **Smart titles**: "Anish (Kirby) vs John (Mario) - Match #42"
- **Detailed descriptions**: Player stats, KOs, Falls, SDs, winner info
- **Auto-playlist**: All videos organized in a YouTube playlist
- **Bulk upload script**: Upload existing videos from `matches/` and `matches/forlater/`

---

## 📝 Prerequisites

Before you begin, make sure you have:
- ✅ A Google account (Gmail)
- ✅ A YouTube channel (create one if needed at youtube.com)
- ✅ Python environment with dependencies installed

---

## 🚀 Step-by-Step Setup

### Step 1: Install New Dependencies

```bash
pip install -r requirements.txt
```

This installs the Google API libraries needed for YouTube uploads.

---

### Step 2: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **"New Project"** in the top navigation
3. **Project name**: `Smash Leaderboard` (or any name you prefer)
4. Click **"Create"**
5. Wait ~30 seconds for the project to be created

---

### Step 3: Enable YouTube Data API

1. Make sure your new project is selected (check top navigation bar)
2. Go to **"APIs & Services" → "Library"** (left sidebar)
3. Search for: `YouTube Data API v3`
4. Click on **"YouTube Data API v3"**
5. Click the **"Enable"** button
6. Wait for it to enable (~10 seconds)

---

### Step 4: Configure OAuth Consent Screen

1. Go to **"APIs & Services" → "OAuth consent screen"** (left sidebar)
2. **User Type**: Select **"External"** (unless you have Google Workspace)
3. Click **"Create"**

#### Fill out the consent screen form:

**App information:**
- **App name**: `Smash Leaderboard`
- **User support email**: Your email address
- **App logo**: (Optional - skip for now)

**App domain:**
- Leave all fields blank (skip)

**Developer contact information:**
- **Email addresses**: Your email address

4. Click **"Save and Continue"**

**Scopes page:**
- Click **"Save and Continue"** (no changes needed)

**Test users page:**
- Click **"+ Add Users"**
- Enter your Gmail address
- Click **"Add"**
- Click **"Save and Continue"**

**Summary page:**
- Review and click **"Back to Dashboard"**

---

### Step 5: Create OAuth Credentials

1. Go to **"APIs & Services" → "Credentials"** (left sidebar)
2. Click **"+ Create Credentials"** at the top
3. Select **"OAuth client ID"**

#### Configure the OAuth client:

- **Application type**: Select **"Desktop app"**
- **Name**: `Smash Leaderboard Desktop`
- Click **"Create"**

4. A dialog will appear showing your Client ID and Client Secret
5. Click **"Download JSON"** button
6. Save the downloaded file

---

### Step 6: Setup Credentials File

1. Rename the downloaded file to `client_secrets.json`
2. Move it to your project root directory (same folder as `capture_card_processor.py`)

**Your project directory should now have:**
```
/smash-leaderboard-ai/
├── capture_card_processor.py
├── youtube_uploader.py
├── bulk_upload_to_youtube.py
├── client_secrets.json          ← NEW FILE
├── .env
└── ...
```

**⚠️ IMPORTANT**: Never commit `client_secrets.json` to git! It's already in `.gitignore`.

---

### Step 7: Add Database Column (One-Time)

You need to add a `youtube_url` column to your `matches` table in Supabase:

**Option A: Using Supabase Dashboard**
1. Go to your Supabase project dashboard
2. Click **"Table Editor"** (left sidebar)
3. Select the **"matches"** table
4. Click **"+ New Column"** button
5. Fill out:
   - **Name**: `youtube_url`
   - **Type**: `text`
   - **Default value**: Leave blank
   - **Nullable**: ✅ Checked (yes)
6. Click **"Save"**

**Option B: Using SQL**
1. Go to **"SQL Editor"** in Supabase
2. Run this query:
```sql
ALTER TABLE matches ADD COLUMN youtube_url TEXT;
```

---

### Step 8: First-Time Authentication

Now you'll authenticate with YouTube. This only needs to be done once.

Run the bulk upload script in dry-run mode:

```bash
python bulk_upload_to_youtube.py --dry-run
```

**What will happen:**
1. Your web browser will open automatically
2. You'll see a Google sign-in page
3. Sign in with the Google account that owns your YouTube channel
4. You'll see a warning: **"Google hasn't verified this app"**
   - Click **"Advanced"**
   - Click **"Go to Smash Leaderboard (unsafe)"**
   - This is normal! It's your own app.
5. Review permissions:
   - ✅ Upload videos to YouTube
6. Click **"Allow"**
7. You'll see "The authentication flow has completed"
8. Close the browser tab

**Credentials saved!** A file `youtube-upload-credentials.pickle` has been created in your project directory. This file stores your access tokens so you don't have to sign in again.

---

## ✅ Testing

### Test 1: Dry Run (No Upload)

Preview what would be uploaded without actually uploading:

```bash
python bulk_upload_to_youtube.py --dry-run
```

Expected output:
```
Found X videos in matches
Found Y videos in matches/forlater
Processing Z video files...
[DRY RUN] Would upload: 20240115_143052.mp4 (Match ID: 42)
...
BULK UPLOAD SUMMARY
Total files found:      Z
Already uploaded:       0
Successfully uploaded:  0
Failed:                 0
Skipped:                0
```

### Test 2: Upload One Video

Upload a single test video from `matches/forlater`:

```bash
python bulk_upload_to_youtube.py --directory matches/forlater
```

Expected output:
```
Found 1 videos in matches/forlater
Processing 1 video files...
Uploading videos: 100%|████████| 1/1
Uploading: 20240115_143052.mp4
Successfully uploaded: https://www.youtube.com/watch?v=...

BULK UPLOAD SUMMARY
Total files found:      1
Successfully uploaded:  1
```

**Verify:**
1. Go to [YouTube Studio](https://studio.youtube.com/)
2. Click **"Content"** (left sidebar)
3. You should see your uploaded video!
4. Check the title, description, and that it's in your playlist

### Test 3: Automatic Upload (Live Capture)

1. Start the capture processor:
```bash
python capture_card_processor.py
```

2. Play a Smash Bros match
3. After the match ends and is processed, you should see:
```
Uploading match video to YouTube...
Successfully uploaded video: https://www.youtube.com/watch?v=...
YouTube URL saved to database
```

4. Verify the video on YouTube

---

## 📊 Bulk Uploading Existing Videos

Once testing is complete, upload all your existing videos:

```bash
# Upload all videos, skip ones already uploaded
python bulk_upload_to_youtube.py --skip-uploaded
```

**Options:**
- `--dry-run`: Preview without uploading
- `--skip-uploaded`: Skip videos already uploaded (default: True)
- `--force`: Re-upload all videos (overrides --skip-uploaded)
- `--directory DIR`: Upload only from specific directory
- `--playlist NAME`: Custom playlist name (default: "Smash Bros Matches")
- `--no-playlist`: Don't add to playlist

**Examples:**

```bash
# Upload only from matches/forlater
python bulk_upload_to_youtube.py --directory matches/forlater

# Preview all uploads
python bulk_upload_to_youtube.py --dry-run

# Force re-upload everything
python bulk_upload_to_youtube.py --force

# Upload without playlist
python bulk_upload_to_youtube.py --no-playlist
```

---

## 📈 Understanding YouTube Quotas

**YouTube API Quota Limits:**
- Default quota: **10,000 units per day**
- Video upload cost: **1,600 units**
- **Maximum uploads per day: ~6 videos**

**What happens when you hit the quota:**
- Uploads will fail with a quota error
- Error is logged but doesn't crash the program
- Videos remain saved locally
- Quota resets at midnight Pacific Time

**Solutions if you need more uploads:**
1. **Request quota increase** from Google (can take days/weeks)
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - APIs & Services → YouTube Data API v3 → Quotas
   - Click "Request quota increase"
2. **Spread uploads** across multiple days
3. **Use multiple Google accounts** with separate credentials

---

## 🎥 Video Details

### Video Title Format

**With player names (preferred):**
```
Anish (Kirby) vs John (Mario) - Match #42
```

**Without player names (fallback):**
```
Match #42 - 2024-01-15 14:30
```

### Video Description Format

```
Super Smash Bros Ultimate Match

Match ID: 42
Date: January 15, 2024 2:30 PM

Players:
- Anish (Kirby) - 3 KOs, 2 Falls, 1 SD - WINNER
- John (Mario) - 2 KOs, 3 Falls, 0 SD

Recorded with automated capture system
```

### Video Settings

- **Category**: Gaming (20)
- **Privacy**: Public
- **Tags**: super smash bros, smash ultimate, ssbu, gameplay, competitive, [player names], [character names]
- **Playlist**: Smash Bros Matches (auto-created)
- **Thumbnail**: Auto-generated by YouTube

---

## 🔍 Troubleshooting

### Issue: "OAuth credentials file not found"

**Solution**: Make sure `client_secrets.json` is in your project root directory.

### Issue: "SUPABASE_URL not found in environment"

**Solution**: Make sure your `.env` file has `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.

### Issue: "Quota exceeded" error

**Solution**: You've hit the daily upload limit (~6 videos). Wait until tomorrow or request a quota increase.

### Issue: Browser doesn't open for authentication

**Solution**: 
1. Look for a URL in the terminal output
2. Copy and paste it into your browser manually
3. Complete the authentication flow

### Issue: "Invalid client_secrets.json format"

**Solution**: Re-download the credentials file from Google Cloud Console. Make sure you selected "Desktop app" not "Web application".

### Issue: Upload fails with "Permission denied"

**Solution**: 
1. Check that you authorized the correct Google account
2. Make sure the account owns the YouTube channel
3. Re-authenticate by deleting `youtube-upload-credentials.pickle` and running the script again

### Issue: Videos upload but don't appear in playlist

**Solution**: 
1. The playlist might not have been created. Check [YouTube Studio](https://studio.youtube.com/) → Playlists
2. Try running: `python bulk_upload_to_youtube.py --playlist "Smash Bros Matches"`

### Issue: "No videos found to upload"

**Solution**: 
1. Check that you have `.mp4` files in `matches/` or `matches/forlater/`
2. Make sure files follow naming format: `YYYYMMDD_HHMMSS.mp4` or `{match_id}-YYYYMMDD_HHMMSS.mp4`
3. Result screen videos are excluded (only main match videos are uploaded)

---

## 🔒 Security Notes

**Keep these files SECRET:**
- `client_secrets.json` - OAuth credentials
- `youtube-upload-credentials.pickle` - Access tokens
- `.env` - Database credentials

**Never:**
- Commit them to git (already in `.gitignore`)
- Share them publicly
- Post them in Discord/Slack/etc

**If credentials are leaked:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. APIs & Services → Credentials
3. Delete the compromised OAuth client
4. Create a new one
5. Download new `client_secrets.json`
6. Re-authenticate

---

## 📚 Additional Resources

- [YouTube Data API Documentation](https://developers.google.com/youtube/v3)
- [Google Cloud Console](https://console.cloud.google.com/)
- [YouTube Studio](https://studio.youtube.com/)
- [OAuth 2.0 for Desktop Apps](https://developers.google.com/youtube/v3/guides/auth/installed-apps)

---

## 🎉 You're All Set!

Your Smash Bros matches will now automatically upload to YouTube! 

**Next steps:**
1. Play some matches and watch them auto-upload
2. Run the bulk upload script to upload old matches
3. Check your YouTube channel to see all your content
4. Share your YouTube links with friends!

If you run into any issues, check the Troubleshooting section above or review the error logs in your terminal.
