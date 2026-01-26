# Fixing "Error 403: access_denied" for YouTube OAuth

## 🔴 The Problem

You're getting `Error 403: access_denied` when trying to authenticate with YouTube. This happens because:

1. Your OAuth consent screen is in "Testing" mode
2. You haven't added your email as a test user
3. Or the OAuth client isn't properly configured

---

## ✅ Solution: Add Test User

### Step 1: Go to OAuth Consent Screen

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Make sure your "Smash Leaderboard" project is selected (top dropdown)
3. Navigate to: **APIs & Services → OAuth consent screen**

### Step 2: Add Test Users

1. Scroll down to **"Test users"** section
2. Click **"+ ADD USERS"**
3. Enter your Gmail address (the one you want to use for YouTube)
4. Click **"Add"**
5. Click **"SAVE"** at the bottom

### Step 3: Verify Settings

Make sure these are correct:

**App information:**
- Publishing status: **Testing** (this is OK for now)
- User type: **External**

**Test users:**
- Your Gmail address should be listed ✅

---

## 🔄 Alternative: Publish the App (Optional)

If you don't want to manage test users, you can publish the app:

### Option A: Publish (No Verification Needed for Personal Use)

1. Go to **OAuth consent screen**
2. Click **"PUBLISH APP"** button
3. Confirm by clicking **"CONFIRM"**

**Note:** For YouTube API with only upload scope, you don't need Google verification. Publishing makes it available to anyone, but only you have the credentials.

---

## 🔍 Verify OAuth Client Configuration

### Check Your OAuth Client Settings

1. Go to **APIs & Services → Credentials**
2. Click on your OAuth 2.0 Client ID
3. Verify:
   - **Application type**: Desktop app ✅
   - **Name**: Smash Leaderboard Desktop (or whatever you named it)

### Check Authorized Redirect URIs

For Desktop app, you should see:
- No redirect URIs needed (Desktop apps use localhost automatically)

---

## 🧪 Test After Fixing

After adding yourself as a test user:

1. **Delete old credentials** (start fresh):
   ```bash
   rm youtube-upload-credentials.pickle
   ```

2. **Try authentication again**:
   ```bash
   uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
   ```

3. **Browser should open** and show:
   - Sign in page
   - Permission request
   - ⚠️ "Google hasn't verified this app" warning
   
4. **Click "Advanced"** → **"Go to Smash Leaderboard (unsafe)"**

5. **Grant permissions** - Check the box for "Upload videos"

6. **Success!** You should see "Authentication successful"

---

## 🎯 Step-by-Step Fix

### Quick Fix (Most Common)

```bash
# 1. Go to Google Cloud Console
open https://console.cloud.google.com/apis/credentials/consent

# 2. Add your Gmail as test user (see instructions above)

# 3. Delete old credentials
rm youtube-upload-credentials.pickle

# 4. Try again
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
```

---

## 🔴 Common Mistakes

### ❌ Wrong User Type Selected
- **Problem**: Selected "Internal" but don't have Google Workspace
- **Fix**: Use "External" user type

### ❌ No Test Users Added
- **Problem**: App is in Testing mode but no test users
- **Fix**: Add your Gmail in OAuth consent screen → Test users

### ❌ Wrong Application Type
- **Problem**: Created "Web application" instead of "Desktop app"
- **Fix**: Delete OAuth client, create new one as "Desktop app"

### ❌ Wrong Google Account
- **Problem**: Trying to sign in with account not listed as test user
- **Fix**: Use the Gmail account you added as test user

### ❌ App Not Published (Alternative)
- **Problem**: Don't want to manage test users
- **Fix**: Publish the app (no verification needed for personal use)

---

## 📝 Detailed OAuth Consent Screen Setup

### 1. App Information
```
App name: Smash Leaderboard
User support email: your-email@gmail.com
Developer contact: your-email@gmail.com
```

### 2. Scopes
- No additional scopes needed (we specify in code)

### 3. Test Users
```
your-email@gmail.com  ← ADD THIS!
```

### 4. Summary
- Review and confirm

---

## 🚨 If Still Not Working

### Try Creating New OAuth Client

1. **Delete existing OAuth client**:
   - Go to Credentials
   - Find your OAuth 2.0 Client ID
   - Click trash icon → Delete

2. **Create new OAuth client**:
   - Click "+ Create Credentials"
   - Select "OAuth client ID"
   - **Application type**: Desktop app
   - **Name**: Smash Leaderboard Desktop
   - Click "Create"

3. **Download new credentials**:
   - Click "Download JSON"
   - Save as `client_secrets.json`
   - Replace the old file in your project root

4. **Try again**:
   ```bash
   rm youtube-upload-credentials.pickle
   uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
   ```

---

## 🎉 Success Indicators

You'll know it worked when:

1. ✅ Browser opens automatically
2. ✅ You can sign in with your Google account
3. ✅ You see "Smash Leaderboard wants to access your Google Account"
4. ✅ You see permission: "Upload YouTube videos"
5. ✅ After clicking "Allow", you see "Authentication successful"
6. ✅ Terminal shows: "Successfully authenticated with YouTube API"

---

## 📞 Need More Help?

If you're still stuck, check:

1. **OAuth Consent Screen Status**:
   - Publishing status: Testing ✅
   - Test users: YOUR_EMAIL@gmail.com ✅

2. **OAuth Client Type**:
   - Application type: Desktop app ✅

3. **API Enabled**:
   - YouTube Data API v3: Enabled ✅

4. **Correct Project Selected**:
   - Check dropdown at top of Google Cloud Console
   - Should show "Smash Leaderboard" or your project name

---

## 🔧 Debug Commands

```bash
# Check if credentials file exists
ls -la client_secrets.json

# View OAuth client ID (should match in error)
cat client_secrets.json | grep client_id

# Remove old credentials to start fresh
rm youtube-upload-credentials.pickle

# Test with verbose output
uv run python -c "from youtube_uploader import get_uploader; get_uploader().get_authenticated_service()"
```

---

## ✅ Quick Checklist

Before trying again, verify:

- [ ] OAuth consent screen configured
- [ ] Your Gmail added as test user
- [ ] OAuth client is "Desktop app" type
- [ ] YouTube Data API v3 is enabled
- [ ] `client_secrets.json` downloaded to project root
- [ ] Old `youtube-upload-credentials.pickle` deleted

Then run:
```bash
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run
```

Good luck! 🚀
