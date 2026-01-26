# Using UV to Install and Run Smash Leaderboard

This guide shows how to use `uv` (the fast Python package installer) to set up and run the Smash Bros Leaderboard project.

## 🚀 What is UV?

`uv` is a blazingly fast Python package installer and resolver, written in Rust. It's a drop-in replacement for `pip` and `pip-tools` that's 10-100x faster.

## 📦 Install UV

If you don't have `uv` installed yet:

### macOS/Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows:
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Using Homebrew (macOS):
```bash
brew install uv
```

### Verify installation:
```bash
uv --version
```

---

## 🎯 Project Setup with UV

### 1. Create a Virtual Environment

```bash
# Create a virtual environment in .venv
uv venv

# Activate it
# On macOS/Linux:
source .venv/bin/activate

# On Windows:
.venv\Scripts\activate
```

### 2. Install Dependencies

```bash
# Install all dependencies from requirements.txt
uv pip install -r requirements.txt
```

This will be much faster than `pip install -r requirements.txt`!

---

## 🎮 Running the Project

### Capture Processor (Main Application)

```bash
# Run with uv (auto-detects virtual environment)
uv run python capture_card_processor.py

# Or with activated venv:
python capture_card_processor.py
```

### YouTube Upload Scripts

```bash
# Create test videos
uv run python create_test_videos.py

# Create test database records
uv run python create_test_database_records.py

# Dry run - preview uploads
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run

# Upload test videos
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads

# Upload all existing videos
uv run python bulk_upload_to_youtube.py --skip-uploaded
```

### Other Utilities

```bash
# ELO manager
uv run python elo_manager.py --rankings

# Process a result video manually
uv run python process_result_video.py path/to/video.mp4

# Recompute ELO ratings
uv run python recompute_all_player_elos.py
```

---

## 🔄 Using `uv run` (Recommended)

The `uv run` command automatically:
- Detects your virtual environment
- Runs the command with the correct Python
- Works even without activating the venv

### Examples:

```bash
# Run any script
uv run python script.py

# Run with arguments
uv run python capture_card_processor.py --test-mode --test-video-path test.mp4

# Run bulk upload with options
uv run python bulk_upload_to_youtube.py --directory matches/forlater --dry-run
```

---

## 📝 Quick Command Reference

### Setup (One-Time)
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Install dependencies
uv pip install -r requirements.txt
```

### Daily Use
```bash
# Activate virtual environment (optional with uv run)
source .venv/bin/activate

# Run capture processor
uv run python capture_card_processor.py

# Upload videos to YouTube
uv run python bulk_upload_to_youtube.py --skip-uploaded

# Check ELO rankings
uv run python elo_manager.py --rankings
```

### Testing
```bash
# Create test videos
uv run python create_test_videos.py

# Test YouTube upload (dry run)
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run

# Upload one test video
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads
```

---

## 🆕 Adding New Dependencies

If you need to add new packages:

```bash
# Add a package
uv pip install package-name

# Add and save to requirements.txt
uv pip install package-name && uv pip freeze > requirements.txt

# Or manually edit requirements.txt and run:
uv pip install -r requirements.txt
```

---

## 🔄 Updating Dependencies

```bash
# Update all packages to latest versions
uv pip install --upgrade -r requirements.txt

# Update a specific package
uv pip install --upgrade package-name
```

---

## 🧹 Cleaning Up

```bash
# Deactivate virtual environment
deactivate

# Remove virtual environment
rm -rf .venv

# Recreate if needed
uv venv
uv pip install -r requirements.txt
```

---

## 💡 UV vs PIP Comparison

| Command | PIP | UV |
|---------|-----|-----|
| Create venv | `python -m venv .venv` | `uv venv` |
| Install deps | `pip install -r requirements.txt` | `uv pip install -r requirements.txt` |
| Run script | `python script.py` | `uv run python script.py` |
| Add package | `pip install package` | `uv pip install package` |
| Speed | Normal | 10-100x faster ⚡ |

---

## 🎯 Complete Setup Example

Here's a complete setup from scratch:

```bash
# 1. Clone/navigate to project
cd smash-leaderboard-ai

# 2. Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Create virtual environment
uv venv

# 4. Install dependencies (fast!)
uv pip install -r requirements.txt

# 5. Set up environment variables
cp .env.example .env  # if available
nano .env  # add your credentials

# 6. Create test videos
uv run python create_test_videos.py

# 7. Test YouTube upload (dry run)
uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run

# 8. Run the main application
uv run python capture_card_processor.py
```

---

## 🐛 Troubleshooting

### "uv: command not found"
**Solution**: Install uv or add it to PATH:
```bash
export PATH="$HOME/.cargo/bin:$PATH"
```

### "No module named 'X'"
**Solution**: Make sure dependencies are installed:
```bash
uv pip install -r requirements.txt
```

### Virtual environment not activated
**Solution**: Either activate it or use `uv run`:
```bash
source .venv/bin/activate  # Activate
# OR
uv run python script.py     # No activation needed
```

### Wrong Python version
**Solution**: Create venv with specific Python:
```bash
uv venv --python 3.11
```

---

## 🎓 Pro Tips

1. **Use `uv run`** - No need to activate virtual environment
2. **Fast installs** - UV caches packages and reuses them across projects
3. **Parallel installs** - UV installs packages in parallel for even faster setup
4. **Lock files** - Consider using `uv pip compile` for reproducible builds

### Generate a lock file (optional):
```bash
# Generate requirements.lock with exact versions
uv pip compile requirements.txt -o requirements.lock

# Install from lock file
uv pip sync requirements.lock
```

---

## 📚 Additional Resources

- [UV Documentation](https://github.com/astral-sh/uv)
- [UV Installation Guide](https://astral.sh/uv)
- Project-specific docs:
  - `YOUTUBE_SETUP.md` - YouTube upload setup
  - `YOUTUBE_QUICK_REFERENCE.md` - YouTube commands
  - `TESTING_YOUTUBE_UPLOAD.md` - Testing guide

---

## 🎉 You're Ready!

With UV installed, you can:
- ✅ Install dependencies 10-100x faster
- ✅ Run scripts without activating venv (`uv run`)
- ✅ Manage packages more efficiently
- ✅ Save time on every operation

Start with:
```bash
uv venv
uv pip install -r requirements.txt
uv run python create_test_videos.py
```

Enjoy the speed! ⚡🚀
