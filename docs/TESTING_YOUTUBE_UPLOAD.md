# YouTube Upload Testing Guide

## 📦 Test Videos Created

I've created 6 dummy test videos for you:

### matches/test_uploads/ (with Match IDs)
- `1-20240115_143052.mp4` (6.5 MB, 8 seconds) - Match #1: Anish vs John
- `2-20240115_150230.mp4` (6.6 MB, 8 seconds) - Match #2: Sarah vs Mike  
- `3-20240116_120000.mp4` (6.6 MB, 8 seconds) - Match #3: Emily vs David

### matches/forlater/ (legacy format, no Match ID)
- `20240114_100000.mp4` (6.6 MB, 7 seconds) - Legacy match (timestamp only)
- `20240114_110000.mp4` (6.6 MB, 7 seconds) - Legacy match (timestamp only)
- `20240114_120000.mp4` (6.5 MB, 7 seconds) - Legacy match (timestamp only)

Each video has:
- ✅ Proper MP4 format (1920x1080, 30fps)
- ✅ Colorful animated frames
- ✅ Text overlay showing match info
- ✅ Frame counter and time display
- ✅ Filename displayed on screen

---

## 🧪 Testing Steps

### Step 1: Install Dependencies (if not done)

```bash
pip install -r requirements.txt
```

### Step 2: Complete Google Cloud Setup

Follow the detailed instructions in `YOUTUBE_SETUP.md`:
1. Create Google Cloud project
2. Enable YouTube Data API v3
3. Create OAuth credentials
4. Download `client_secrets.json` to project root

### Step 3: Add Database Column (if not done)

In Supabase, run:
```sql
ALTER TABLE matches ADD COLUMN youtube_url TEXT;
```

### Step 4: (Optional) Create Test Database Records

If you want full metadata in the uploads, create test database records:

```bash
python create_test_database_records.py
```

This creates:
- Test players: Anish, John, Sarah, Mike, Emily, David
- Match #1: Anish (Kirby) vs John (Mario) - Anish wins
- Match #2: Sarah (Pikachu) vs Mike (Link) - Sarah wins  
- Match #3: Emily (Samus) vs David (Fox) - David wins

**Note**: This requires your `.env` file to have valid Supabase credentials.

---

## 🎬 Test Scenarios

### Test 1: Dry Run (No Upload)

Preview what would be uploaded **without actually uploading**:

```bash
python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
```

**Expected output:**
```
*** DRY RUN MODE - No videos will be uploaded ***

Found 3 videos in matches/test_uploads
Processing 3 video files...
[DRY RUN] Would upload: 1-20240115_143052.mp4 (Match ID: 1)
[DRY RUN] Would upload: 2-20240115_150230.mp4 (Match ID: 2)
[DRY RUN] Would upload: 3-20240116_120000.mp4 (Match ID: 3)

BULK UPLOAD SUMMARY
Total files found:      3
Successfully uploaded:  0
```

### Test 2: First-Time Authentication

This will trigger the OAuth flow (only needed once):

```bash
python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
```

**What happens:**
1. Browser opens automatically
2. Sign in with your Google account
3. Authorize the app
4. Credentials saved to `youtube-upload-credentials.pickle`
5. Future uploads won't require browser sign-in

### Test 3: Upload One Test Video

Upload just one video to verify everything works:

```bash
# Upload only the first test video
python bulk_upload_to_youtube.py --directory matches/test_uploads
```

**Expected output:**
```
Found 3 videos in matches/test_uploads
Processing 3 video files...
Uploading: 1-20240115_143052.mp4
Successfully uploaded: https://www.youtube.com/watch?v=...

BULK UPLOAD SUMMARY
Total files found:      3
Successfully uploaded:  1
```

**Verify:**
1. Go to [YouTube Studio](https://studio.youtube.com/)
2. Click "Content"
3. You should see the uploaded video!
4. Check the title, description, and playlist

### Test 4: Test Legacy Videos (Timestamp Matching)

Test legacy videos without match IDs:

```bash
python bulk_upload_to_youtube.py --directory matches/forlater --dry-run
```

These videos will:
- Try to match to database by timestamp (±5 seconds)
- If no match found, upload with generic title/description
- Use timestamp as pseudo-match-ID

### Test 5: Upload All Test Videos

Upload all test videos at once:

```bash
python bulk_upload_to_youtube.py --directory matches/test_uploads matches/forlater
```

**Note**: Be aware of YouTube quota limits (~6 uploads per day).

### Test 6: Test Skip Already Uploaded

Try uploading again with `--skip-uploaded`:

```bash
python bulk_upload_to_youtube.py --directory matches/test_uploads --skip-uploaded
```

**Expected output:**
```
Skipping already uploaded: 1-20240115_143052.mp4
...

BULK UPLOAD SUMMARY
Total files found:      3
Already uploaded:       1
Successfully uploaded:  2
```

### Test 7: Test Playlist Creation

Check that videos are added to playlist:

```bash
python bulk_upload_to_youtube.py --directory matches/test_uploads --playlist "Test Playlist"
```

Verify in [YouTube Studio](https://studio.youtube.com/) → Playlists

---

## 🔍 What to Check

After uploading, verify these details on YouTube:

### Video Title
- **With database record**: "Anish (Kirby) vs John (Mario) - Match #1"
- **Without database record**: "Match #1 - 2024-01-15 14:30"

### Video Description
```
Super Smash Bros Ultimate Match

Match ID: 1
Date: January 15, 2024 2:30 PM

Players:
- Anish (Kirby) - 3 KOs, 2 Falls, 0 SD - WINNER
- John (Mario) - 2 KOs, 3 Falls, 1 SD

Recorded with automated capture system
```

### Video Settings
- ✅ Privacy: Public
- ✅ Category: Gaming
- ✅ In playlist: "Smash Bros Matches" (or custom name)
- ✅ Tags: super smash bros, smash ultimate, ssbu, gameplay, etc.

### Database
Check Supabase `matches` table:
- ✅ `youtube_url` column populated with YouTube link

---

## 🧹 Cleanup After Testing

Once you've verified everything works, you can delete the test videos:

### From YouTube:
1. Go to [YouTube Studio](https://studio.youtube.com/)
2. Click "Content"
3. Select test videos
4. Click "⋮" → "Delete forever"

### From Local Files:
```bash
rm -rf matches/test_uploads/
rm matches/forlater/20240114_*.mp4
```

### From Database:
```sql
DELETE FROM match_participants WHERE match_id IN (1, 2, 3);
DELETE FROM matches WHERE id IN (1, 2, 3);
```

---

## 🚨 Troubleshooting Test Issues

**"OAuth credentials file not found"**
→ Make sure `client_secrets.json` is in project root

**"SUPABASE_URL not found"**  
→ Check your `.env` file (only needed for database record script)

**"No videos found to upload"**
→ Run `python create_test_videos.py` again

**Browser doesn't open for authentication**
→ Copy the URL from terminal and paste in browser

**Upload fails immediately**
→ Check your internet connection and YouTube API quota

**Video appears on YouTube but no metadata**
→ Database records not created. Run `create_test_database_records.py`

---

## ✅ Expected Test Results

After running all tests, you should have:

- ✅ 6 test videos uploaded to YouTube
- ✅ Videos organized in "Smash Bros Matches" playlist
- ✅ Proper titles and descriptions
- ✅ YouTube URLs saved to database (for videos with match IDs)
- ✅ Authentication working smoothly
- ✅ Confidence that the system works!

---

## 🎉 Next Steps

Once testing is complete:

1. **Delete test videos** (see Cleanup section above)
2. **Upload real matches**: `python bulk_upload_to_youtube.py --skip-uploaded`
3. **Start capture processor**: Videos will auto-upload from now on!
4. **Share your YouTube channel** with friends

---

## 📝 Regenerate Test Videos

If you need to recreate the test videos:

```bash
python create_test_videos.py
```

This will regenerate all 6 test videos in the proper directories.

---

## 🔧 Utility Scripts Created

- `create_test_videos.py` - Generate dummy MP4 files
- `create_test_database_records.py` - Create test match records in Supabase
- Both scripts are safe to run multiple times (they skip existing records)

---

Have fun testing! 🚀
