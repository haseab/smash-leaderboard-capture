# Real-time live leaderboard for Super Smash Bros Ultimate

## Tools
- Nintendo Switch
- EVGA Capture Card
- Cloudflare Worker Gemini proxy (default model: Gemini 3.1 Pro Preview)

## How it works
Matches are automatically detected & recorded by a computer program connected to the capture card that is continuously monitoring the nintendo switch. 
Once a match is finished, a clip plus high-resolution stills from the results screen are sent to Gemini to extract stats from, after which the leaderboard is updated based on the match's stats.

## Gemini proxy

Do not put `GEMINI_API_KEY` on a shared capture computer. Deploy `cloudflare/gemini-proxy-worker.js` as a Cloudflare Worker, set `GEMINI_API_KEY` and `CLIENT_AUTH_TOKEN` as Worker secrets, then configure only these values on the capture machine:

```bash
GEMINI_PROXY_URL=https://your-worker.your-subdomain.workers.dev/analyze
GEMINI_PROXY_TOKEN=replace-with-the-client-token
```

When `GEMINI_PROXY_URL` is present, the capture scripts send the result video/stills to the Worker and never initialize a local Gemini API client. See `docs/GEMINI_PROXY.md` for setup details.

## Audio capture

Live match recordings can include capture-card audio when ffmpeg is installed and an audio input is configured.

On Windows, list DirectShow audio devices:

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

Then run with the exact device name:

```powershell
uv run python capture_card_processor.py --audio-device "EVGA XR1 Capture Box Audio"
```

You can also set `CAPTURE_AUDIO_DEVICE` in `.env`. Audio is skipped in test mode and when no audio device is configured.
