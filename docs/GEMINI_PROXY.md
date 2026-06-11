# Gemini Proxy Setup

The shared capture computer should not store `GEMINI_API_KEY`. Use the Cloudflare Worker in `cloudflare/gemini-proxy-worker.js` as the only service that knows the real Gemini key.

## Worker secrets and variables

Required Worker secrets:

```bash
wrangler secret put GEMINI_API_KEY
wrangler secret put CLIENT_AUTH_TOKEN
```

Optional Worker variables:

```bash
GEMINI_MODEL=gemini-3.1-pro-preview
GEMINI_FALLBACK_MODELS=gemini-3-pro-preview,gemini-3.1-flash-lite,gemini-3-flash-preview
GEMINI_API_VERSION=v1beta
RATE_LIMIT_PER_MINUTE=10
MAX_UPLOAD_BYTES=83886080
```

For stronger distributed rate limiting, bind a Workers KV namespace named `RATE_LIMIT_KV`. Without that binding, the Worker still rate-limits in memory per isolate.

## Capture computer environment

Remove `GEMINI_API_KEY` from the shared machine and set only:

```bash
GEMINI_PROXY_URL=https://your-worker.your-subdomain.workers.dev/analyze
GEMINI_PROXY_TOKEN=the-same-value-as-client-auth-token
```

The token is still a credential, but it can only call this proxy endpoint and can be rotated without rotating the Gemini key.

## Local behavior

`gemini_match_analyzer.py` now checks `GEMINI_PROXY_URL` first. If it is set, capture scripts upload the preprocessed result clip, optional context image, and result stills to the Worker. If it is not set, the old local `GEMINI_API_KEY` path is still available for trusted machines.

## Compare direct Gemini and Worker output

To verify the Worker is a drop-in replacement, put real result-screen clips in `testvideos/`, then run:

```bash
python compare_gemini_backends.py
```

The comparison script requires all three values because it calls both backends:

```bash
GEMINI_API_KEY=your_direct_key
GEMINI_PROXY_URL=https://your-worker.your-subdomain.workers.dev/analyze
GEMINI_PROXY_TOKEN=your_client_token
```

It writes `testvideos/gemini_backend_comparison.json` and exits non-zero if the direct SDK output and Worker output differ for any video. If a test clip has a companion context image, name it with the same stem as the video, or use `context.png`, `frame_42.png`, or `player_context.png` in the same folder.
