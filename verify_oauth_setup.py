#!/usr/bin/env python3
"""
Verify OAuth Setup for YouTube Upload
Checks common issues and provides helpful feedback
"""

import os
import json
import sys

def check_client_secrets():
    """Check if client_secrets.json exists and is valid"""
    print("🔍 Checking OAuth credentials...")
    
    if not os.path.exists('client_secrets.json'):
        print("❌ ERROR: client_secrets.json not found!")
        print("\n📝 Solution:")
        print("1. Go to Google Cloud Console → APIs & Services → Credentials")
        print("2. Download your OAuth 2.0 Client ID credentials")
        print("3. Save as 'client_secrets.json' in project root")
        return False
    
    try:
        with open('client_secrets.json', 'r') as f:
            data = json.load(f)
        
        # Check structure
        if 'installed' in data:
            client_type = 'installed'
            print("✅ Found Desktop app credentials")
        elif 'web' in data:
            client_type = 'web'
            print("⚠️  WARNING: Found Web app credentials")
            print("   You should use Desktop app for this project!")
            return False
        else:
            print("❌ ERROR: Invalid client_secrets.json format")
            return False
        
        # Get client info
        client_info = data[client_type]
        client_id = client_info.get('client_id', 'Unknown')
        
        print(f"   Client ID: {client_id[:50]}...")
        
        # Check required fields
        required = ['client_id', 'client_secret', 'auth_uri', 'token_uri']
        missing = [field for field in required if field not in client_info]
        
        if missing:
            print(f"❌ ERROR: Missing required fields: {', '.join(missing)}")
            return False
        
        print("✅ client_secrets.json is valid")
        return True
        
    except json.JSONDecodeError:
        print("❌ ERROR: client_secrets.json is not valid JSON")
        return False
    except Exception as e:
        print(f"❌ ERROR: Failed to read client_secrets.json: {e}")
        return False


def check_youtube_api():
    """Check if YouTube Data API is enabled"""
    print("\n🔍 Checking YouTube Data API...")
    print("⚠️  Cannot verify automatically - please check manually:")
    print("   1. Go to: https://console.cloud.google.com/apis/library")
    print("   2. Search for 'YouTube Data API v3'")
    print("   3. Ensure it shows 'API enabled' (green checkmark)")


def check_oauth_consent():
    """Provide instructions for OAuth consent screen"""
    print("\n🔍 Checking OAuth Consent Screen setup...")
    print("⚠️  Cannot verify automatically - please check manually:")
    print("\n   Go to: https://console.cloud.google.com/apis/credentials/consent")
    print("\n   Verify these settings:")
    print("   ✓ User type: External")
    print("   ✓ Publishing status: Testing (or Published)")
    print("   ✓ Test users: Your Gmail address added")
    print("\n   📝 If your email is NOT in test users:")
    print("      1. Click 'ADD USERS' in Test users section")
    print("      2. Enter your Gmail address")
    print("      3. Click 'Add' then 'Save'")


def check_credentials_file():
    """Check if cached credentials exist"""
    print("\n🔍 Checking cached credentials...")
    
    if os.path.exists('youtube-upload-credentials.pickle'):
        print("ℹ️  Found cached credentials: youtube-upload-credentials.pickle")
        print("   If you're having auth issues, delete this file:")
        print("   rm youtube-upload-credentials.pickle")
    else:
        print("✅ No cached credentials (will authenticate on first run)")


def check_env():
    """Check environment setup"""
    print("\n🔍 Checking environment...")
    
    # Check virtual environment
    in_venv = hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )
    
    if in_venv:
        print(f"✅ Running in virtual environment: {sys.prefix}")
    else:
        print("⚠️  Not in virtual environment")
        print("   Consider using: uv run python script.py")
    
    # Check required packages
    try:
        import google.auth
        print("✅ google-auth installed")
    except ImportError:
        print("❌ google-auth NOT installed")
        print("   Run: uv pip install -r requirements.txt")
        return False
    
    try:
        import google_auth_oauthlib
        print("✅ google-auth-oauthlib installed")
    except ImportError:
        print("❌ google-auth-oauthlib NOT installed")
        print("   Run: uv pip install -r requirements.txt")
        return False
    
    try:
        import googleapiclient
        print("✅ google-api-python-client installed")
    except ImportError:
        print("❌ google-api-python-client NOT installed")
        print("   Run: uv pip install -r requirements.txt")
        return False
    
    return True


def main():
    print("="*60)
    print("🔍 YouTube OAuth Setup Verification")
    print("="*60)
    print()
    
    all_good = True
    
    # Run checks
    if not check_client_secrets():
        all_good = False
    
    check_youtube_api()
    check_oauth_consent()
    check_credentials_file()
    
    if not check_env():
        all_good = False
    
    print("\n" + "="*60)
    
    if all_good:
        print("✅ Basic setup looks good!")
        print("="*60)
        print("\n📝 Next steps:")
        print("\n1. Verify OAuth consent screen (see above)")
        print("2. Make sure your Gmail is added as test user")
        print("3. Delete cached credentials if having issues:")
        print("   rm youtube-upload-credentials.pickle")
        print("\n4. Try authentication:")
        print("   uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run")
    else:
        print("❌ Some issues found - please fix them first")
        print("="*60)
        print("\nSee FIXING_OAUTH_403.md for detailed instructions")
    
    print()


if __name__ == "__main__":
    main()
