#!/bin/bash
# Quick Start Script for Smash Leaderboard with UV

echo "🎮 Smash Leaderboard - Quick Start with UV"
echo "=========================================="
echo ""

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ UV not found. Installing UV..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

echo "✅ UV is installed"
echo ""

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    uv venv
    echo ""
fi

echo "✅ Virtual environment ready"
echo ""

# Install dependencies
echo "📥 Installing dependencies..."
uv pip install -r requirements.txt
echo ""

echo "=========================================="
echo "✅ Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Create test videos:"
echo "   uv run python create_test_videos.py"
echo ""
echo "2. Complete YouTube setup (see YOUTUBE_SETUP.md):"
echo "   - Create Google Cloud project"
echo "   - Enable YouTube Data API"
echo "   - Download client_secrets.json"
echo ""
echo "3. Test YouTube upload (dry run):"
echo "   uv run python bulk_upload_to_youtube.py --directory matches/test_uploads --dry-run"
echo ""
echo "4. Run the capture processor:"
echo "   uv run python capture_card_processor.py"
echo ""
echo "📚 Documentation:"
echo "   - UV_SETUP.md - UV usage guide"
echo "   - YOUTUBE_SETUP.md - YouTube setup instructions"
echo "   - TESTING_YOUTUBE_UPLOAD.md - Testing guide"
echo ""
