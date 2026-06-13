const GEMINI_API_HOST = "https://generativelanguage.googleapis.com";
const DEFAULT_GEMINI_MODEL = "gemini-3.1-pro-preview";
const DEFAULT_GEMINI_FALLBACK_MODELS = [
  "gemini-3-pro-preview",
  "gemini-3.1-flash-lite",
  "gemini-3-flash-preview",
];
const DEFAULT_API_VERSION = "v1beta";
const DEFAULT_UPLOAD_TIMEOUT_SECONDS = 300;
const DEFAULT_FILE_POLL_INTERVAL_SECONDS = 5;
const DEFAULT_RATE_LIMIT_PER_MINUTE = 10;
const DEFAULT_MAX_UPLOAD_BYTES = 80 * 1024 * 1024;

const memoryRateLimits = new Map();

const MATCH_STATS_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    is_online_match: {
      type: "boolean",
      description: "True if any player tag is exactly onlineacc. False for offlineacc or normal player tags.",
    },
    players: {
      type: "array",
      minItems: 1,
      maxItems: 8,
      items: {
        type: "object",
        additionalProperties: false,
        properties: {
          smash_character: {
            type: "string",
            description: "The Super Smash Bros Ultimate character shown on this player card.",
          },
          player_name: {
            type: "string",
            description: "The player tag shown beside P1/P2/P3/P4 and below the character name.",
          },
          is_cpu: {
            type: "boolean",
            description: "True only when the player card explicitly says CPU.",
          },
          total_kos: {
            type: "integer",
            minimum: 0,
            description: "Total KOs shown on the card.",
          },
          total_falls: {
            type: "integer",
            minimum: 0,
            description: "Total falls shown on the card as a non-negative integer.",
          },
          total_sds: {
            type: "integer",
            minimum: 0,
            description: "Total self-destructs shown on the card.",
          },
          has_won: {
            type: "boolean",
            description: "True only for the card with a gold rank 1 winner marker.",
          },
        },
        required: [
          "smash_character",
          "player_name",
          "is_cpu",
          "total_kos",
          "total_falls",
          "total_sds",
          "has_won",
        ],
      },
    },
  },
  required: ["is_online_match", "players"],
};

export default {
  async fetch(request, env, ctx) {
    try {
      return await handleRequest(request, env, ctx);
    } catch (error) {
      const status = errorStatus(error);
      const headers = error instanceof HttpError ? error.headers : {};
      const payload = errorPayload(error);
      if (!(error instanceof HttpError) || status >= 500) {
        console.error("Gemini proxy error", {
          message: error?.message,
          status,
          payload: error instanceof GeminiApiError ? error.payload : undefined,
          stack: error?.stack,
        });
      }
      return jsonResponse(payload, status, headers);
    }
  },
};

async function handleRequest(request, env, ctx) {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }

  const url = new URL(request.url);
  if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
    return jsonResponse({ ok: true });
  }

  if (request.method !== "POST") {
    throw new HttpError(405, "Method not allowed");
  }
  if (url.pathname !== "/" && url.pathname !== "/analyze") {
    throw new HttpError(404, "Not found");
  }
  if (!env.GEMINI_API_KEY) {
    throw new HttpError(500, "GEMINI_API_KEY Worker secret is not configured");
  }

  const tokenDigest = await authenticate(request, env);
  await enforceRateLimit(tokenDigest, env);

  const upload = await parseAnalyzeUpload(request, env);
  const uploadedFiles = [];

  try {
    const parts = [];
    if (upload.contextImage) {
      const file = await uploadAndWait(upload.contextImage, "player_context_image", env);
      uploadedFiles.push(file);
      parts.push(filePart(file));
    }

    for (let index = 0; index < upload.resultStills.length; index += 1) {
      const file = await uploadAndWait(upload.resultStills[index], `result_screen_still_${index + 1}`, env);
      uploadedFiles.push(file);
      parts.push(filePart(file));
    }

    const videoFile = await uploadAndWait(upload.resultVideo, "result_screen_slowed", env);
    uploadedFiles.push(videoFile);
    parts.push(filePart(videoFile, upload.metadata.video_sample_fps));

    parts.push({
      text:
        typeof upload.metadata.prompt === "string" && upload.metadata.prompt.trim()
          ? upload.metadata.prompt
          : buildPrompt({
              hasPlayerContextImage: Boolean(upload.contextImage),
              resultStillCount: upload.resultStills.length,
              playerNameExamples: upload.metadata.player_name_examples,
            }),
    });

    const result = await generateWithFallback(parts, env);
    const matchStats = parseMatchStats(result.response);
    validateMatchStats(matchStats);

    return jsonResponse({
      match_stats: matchStats,
      model: result.model,
    });
  } finally {
    ctx.waitUntil(deleteUploadedFiles(uploadedFiles, env));
  }
}

async function parseAnalyzeUpload(request, env) {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("multipart/form-data")) {
    throw new HttpError(415, "Expected multipart/form-data");
  }

  const declaredLength = Number(request.headers.get("content-length") || 0);
  const maxUploadBytes = positiveInt(env.MAX_UPLOAD_BYTES, DEFAULT_MAX_UPLOAD_BYTES);
  if (declaredLength > maxUploadBytes) {
    throw new HttpError(413, "Request is too large");
  }

  const form = await request.formData();
  const metadata = parseMetadata(form.get("metadata"));
  const resultVideo = form.get("result_video");
  if (!isFile(resultVideo) || resultVideo.size === 0) {
    throw new HttpError(400, "Missing result_video file");
  }

  const rawContextImage = form.get("context_image");
  const contextImage = isFile(rawContextImage) && rawContextImage.size > 0 ? rawContextImage : null;
  const resultStills = form.getAll("result_still").filter((value) => isFile(value) && value.size > 0);

  const totalBytes =
    resultVideo.size +
    (contextImage ? contextImage.size : 0) +
    resultStills.reduce((total, file) => total + file.size, 0);
  if (totalBytes > maxUploadBytes) {
    throw new HttpError(413, "Uploaded files are too large");
  }

  return { metadata, resultVideo, contextImage, resultStills };
}

function parseMetadata(rawValue) {
  if (!rawValue || typeof rawValue !== "string") {
    return {};
  }
  try {
    const parsed = JSON.parse(rawValue);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    throw new HttpError(400, "metadata must be valid JSON");
  }
}

function isFile(value) {
  return value && typeof value === "object" && typeof value.arrayBuffer === "function" && typeof value.size === "number";
}

async function authenticate(request, env) {
  const authHeader = request.headers.get("authorization") || "";
  const match = authHeader.match(/^Bearer\s+(.+)$/i);
  if (!match) {
    throw new HttpError(401, "Missing bearer token", { "WWW-Authenticate": "Bearer" });
  }

  const allowedTokens = getAllowedTokens(env);
  if (allowedTokens.length === 0) {
    throw new HttpError(500, "CLIENT_AUTH_TOKEN Worker secret is not configured");
  }

  const presentedDigest = await sha256Hex(match[1].trim());
  for (const token of allowedTokens) {
    if (presentedDigest === await sha256Hex(token)) {
      return presentedDigest;
    }
  }

  throw new HttpError(401, "Invalid bearer token", { "WWW-Authenticate": "Bearer" });
}

function getAllowedTokens(env) {
  const tokens = [];
  if (env.CLIENT_AUTH_TOKEN) {
    tokens.push(env.CLIENT_AUTH_TOKEN);
  }
  if (env.CLIENT_AUTH_TOKENS) {
    tokens.push(...String(env.CLIENT_AUTH_TOKENS).split(/[\n,]/));
  }
  return tokens.map((token) => token.trim()).filter(Boolean);
}

async function enforceRateLimit(tokenDigest, env) {
  const limit = positiveInt(env.RATE_LIMIT_PER_MINUTE, DEFAULT_RATE_LIMIT_PER_MINUTE);
  if (limit <= 0) {
    return;
  }

  const windowSeconds = 60;
  const nowSeconds = Math.floor(Date.now() / 1000);
  const bucket = Math.floor(nowSeconds / windowSeconds);
  const key = `gemini-proxy:${tokenDigest.slice(0, 16)}:${bucket}`;
  const expiresIn = windowSeconds - (nowSeconds % windowSeconds);

  if (env.RATE_LIMIT_KV) {
    const current = Number((await env.RATE_LIMIT_KV.get(key)) || "0");
    if (current >= limit) {
      throw new HttpError(429, "Rate limit exceeded", { "Retry-After": String(expiresIn) });
    }
    await env.RATE_LIMIT_KV.put(key, String(current + 1), { expirationTtl: windowSeconds + 30 });
    return;
  }

  const current = memoryRateLimits.get(key) || { count: 0, expiresAt: Date.now() + expiresIn * 1000 };
  if (current.count >= limit) {
    throw new HttpError(429, "Rate limit exceeded", { "Retry-After": String(expiresIn) });
  }
  current.count += 1;
  memoryRateLimits.set(key, current);

  for (const [entryKey, entry] of memoryRateLimits.entries()) {
    if (entry.expiresAt <= Date.now()) {
      memoryRateLimits.delete(entryKey);
    }
  }
}

async function uploadAndWait(file, displayName, env) {
  const apiVersion = getApiVersion(env);
  const startResponse = await fetch(`${GEMINI_API_HOST}/upload/${apiVersion}/files`, {
    method: "POST",
    headers: {
      "x-goog-api-key": env.GEMINI_API_KEY,
      "X-Goog-Upload-Protocol": "resumable",
      "X-Goog-Upload-Command": "start",
      "X-Goog-Upload-Header-Content-Length": String(file.size),
      "X-Goog-Upload-Header-Content-Type": file.type || "application/octet-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      file: {
        display_name: safeDisplayName(displayName),
      },
    }),
  });

  await assertGeminiOk(startResponse, "Gemini file upload start failed");
  const uploadUrl = startResponse.headers.get("x-goog-upload-url");
  if (!uploadUrl) {
    throw new HttpError(502, "Gemini did not return an upload URL");
  }

  const uploadResponse = await fetch(uploadUrl, {
    method: "POST",
    headers: {
      "Content-Length": String(file.size),
      "X-Goog-Upload-Offset": "0",
      "X-Goog-Upload-Command": "upload, finalize",
    },
    body: file,
  });
  const uploadPayload = await readGeminiJson(uploadResponse, "Gemini file upload failed");
  const apiFile = uploadPayload.file || uploadPayload;
  if (!apiFile.name || !apiFile.uri) {
    throw new HttpError(502, "Gemini file upload response was missing file metadata");
  }

  return waitForFileActive(apiFile, env);
}

async function waitForFileActive(apiFile, env) {
  const timeoutSeconds = positiveInt(env.GEMINI_UPLOAD_TIMEOUT_SECONDS, DEFAULT_UPLOAD_TIMEOUT_SECONDS);
  const pollIntervalSeconds = positiveInt(env.GEMINI_FILE_POLL_INTERVAL_SECONDS, DEFAULT_FILE_POLL_INTERVAL_SECONDS);
  const deadline = Date.now() + timeoutSeconds * 1000;
  let file = apiFile;

  while (Date.now() < deadline) {
    const state = String(file.state || "").toUpperCase();
    if (state === "ACTIVE") {
      return file;
    }
    if (state === "FAILED") {
      throw new HttpError(502, `Gemini file processing failed for ${file.name}`);
    }
    if (!state && file.uri) {
      return file;
    }

    await sleep(Math.min(pollIntervalSeconds * 1000, Math.max(0, deadline - Date.now())));
    const response = await fetch(`${apiBase(env)}/${file.name}`, {
      headers: {
        "x-goog-api-key": env.GEMINI_API_KEY,
      },
    });
    const payload = await readGeminiJson(response, "Gemini file polling failed");
    file = payload.file || payload;
  }

  throw new HttpError(504, `Timed out waiting for Gemini file ${apiFile.name} to become ACTIVE`);
}

function filePart(apiFile, videoSampleFps = null) {
  const part = {
    fileData: {
      mimeType: apiFile.mimeType || apiFile.mime_type || "application/octet-stream",
      fileUri: apiFile.uri,
    },
  };

  const fps = Number(videoSampleFps);
  if (Number.isFinite(fps) && fps > 0) {
    part.videoMetadata = { fps };
  }

  return part;
}

async function generateWithFallback(parts, env) {
  const models = orderedModels(env);
  let lastRateLimitError = null;

  for (const model of models) {
    try {
      const response = await callGenerateContent(model, parts, env, "responseJsonSchema");
      return { model, response };
    } catch (error) {
      if (error instanceof GeminiApiError && error.status === 400) {
        const response = await callGenerateContent(model, parts, env, "responseSchema");
        return { model, response };
      }
      if (isRateLimitError(error)) {
        lastRateLimitError = error;
        continue;
      }
      throw error;
    }
  }

  if (lastRateLimitError) {
    throw new HttpError(429, "All configured Gemini models are rate limited", { "Retry-After": "60" });
  }
  throw new HttpError(502, "No Gemini model returned a response");
}

async function callGenerateContent(model, parts, env, schemaMode) {
  const generationConfig =
    schemaMode === "responseJsonSchema"
      ? {
          temperature: 0,
          responseMimeType: "application/json",
          responseJsonSchema: MATCH_STATS_SCHEMA,
        }
      : {
          temperature: 0,
          responseMimeType: "application/json",
          responseSchema: MATCH_STATS_SCHEMA,
        };

  const response = await fetch(`${apiBase(env)}/${modelPath(model)}:generateContent`, {
    method: "POST",
    headers: {
      "x-goog-api-key": env.GEMINI_API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      contents: [
        {
          role: "user",
          parts,
        },
      ],
      generationConfig,
    }),
  });

  return readGeminiJson(response, "Gemini generateContent failed");
}

function parseMatchStats(generateResponse) {
  const parts = generateResponse?.candidates?.[0]?.content?.parts || [];
  const text = parts.map((part) => part.text || "").join("").trim();
  if (!text) {
    throw new HttpError(502, "Gemini response did not contain text JSON");
  }

  try {
    return JSON.parse(stripJsonCodeFence(text));
  } catch {
    throw new HttpError(502, "Gemini response was not valid JSON");
  }
}

function validateMatchStats(matchStats) {
  if (!matchStats || typeof matchStats !== "object" || !Array.isArray(matchStats.players)) {
    throw new HttpError(502, "Gemini response did not match the expected match stats shape");
  }
  if (typeof matchStats.is_online_match !== "boolean") {
    throw new HttpError(502, "Gemini response is missing is_online_match");
  }
  for (const player of matchStats.players) {
    const valid =
      player &&
      typeof player.smash_character === "string" &&
      typeof player.player_name === "string" &&
      typeof player.is_cpu === "boolean" &&
      Number.isInteger(player.total_kos) &&
      Number.isInteger(player.total_falls) &&
      Number.isInteger(player.total_sds) &&
      typeof player.has_won === "boolean";
    if (!valid) {
      throw new HttpError(502, "Gemini response contained invalid player stats");
    }
  }
}

async function deleteUploadedFiles(files, env) {
  await Promise.allSettled(
    files
      .filter((file) => file && file.name)
      .map((file) =>
        fetch(`${apiBase(env)}/${file.name}`, {
          method: "DELETE",
          headers: {
            "x-goog-api-key": env.GEMINI_API_KEY,
          },
        })
      )
  );
}

function buildPrompt({ hasPlayerContextImage, resultStillCount, playerNameExamples }) {
  const playerContextNote = hasPlayerContextImage
    ? `
I included one early-match frame captured around frame 42. Use it only to identify player names when the results screen advances too quickly or the tags are clearer in that frame.
`
    : "";

  const resultStillNote =
    resultStillCount > 0
      ? `
I also included ${resultStillCount} full-resolution still PNG frame(s) sampled from the results screen. Prefer these still images for reading player names, character names, KOs, Falls, SDs, and the winner marker. Use the video to resolve menu transitions or values that appear only briefly.
`
      : "";

  const examples = playerNameExamples || "habeas, shafaq, jmoon, subby, keneru, and kento";

  return `Here is a Super Smash Bros Ultimate results screen capture.
${playerContextNote}${resultStillNote}
Return exactly one JSON object that matches this shape:

{
  "is_online_match": boolean,
  "players": [
    {
      "smash_character": string,
      "player_name": string,
      "is_cpu": boolean,
      "total_kos": integer,
      "total_falls": integer,
      "total_sds": integer,
      "has_won": boolean
    }
  ]
}

Rules:
- Return one player object for every visible player card. Do not omit a human player just because their card is partially obscured or the result menu advances quickly.
- Player names are listed beside P1, P2, P3, etc and under the actual Smash character name. Player names are not P1/P2/P3/P4 and are not character names.
- Examples of known player names are ${examples}. These are examples, not a closed list.
- Zelda, Joker, Lucina, Donkey Kong, and similar labels are Smash character names, not player names.
- total_kos, total_falls, and total_sds must be non-negative integers. Never return null.
- If a numeric stat is not visible, count the mini character icons under that stat's section. If neither a number nor icons are visible, return 0.
- If the screen displays a negative Falls value, return the positive absolute value.
- has_won is true only for the card with a gold rank 1 winner marker at the top right. If no player has that marker, this is a no-contest and every player has has_won=false.
- is_online_match is true if any player name is exactly "onlineacc". It is false for "offlineacc" and for all normal player tags.
- is_cpu is true only if the player card explicitly says "CPU". Otherwise it is false.
- If all people playing have player names and no card says CPU, then is_cpu must be false for every player and there should be at least two players.
- If you see "mmmmm" as a player name, it has exactly five m letters.
- If a rectangular player card shows "READY FOR THE NEXT BATTLE" for the entire video instead of KOs, Falls, and SDs, set that card to player_name="unknown", smash_character="unknown", total_kos=0, total_falls=0, total_sds=0, is_cpu=false, and has_won=false.
`;
}

async function readGeminiJson(response, message) {
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { raw: text.slice(0, 1000) };
    }
  }

  if (!response.ok) {
    throw new GeminiApiError(response.status, message, payload);
  }
  return payload;
}

async function assertGeminiOk(response, message) {
  if (!response.ok) {
    await readGeminiJson(response, message);
  }
}

function isRateLimitError(error) {
  const payload = error instanceof GeminiApiError ? JSON.stringify(error.payload || {}) : String(error?.message || "");
  return error?.status === 429 || payload.includes("RESOURCE_EXHAUSTED") || payload.includes("Too Many Requests");
}

function orderedModels(env) {
  const primary = String(env.GEMINI_MODEL || DEFAULT_GEMINI_MODEL).trim();
  const fallbackModels = String(env.GEMINI_FALLBACK_MODELS || DEFAULT_GEMINI_FALLBACK_MODELS.join(","))
    .split(",")
    .map((model) => model.trim())
    .filter(Boolean);
  return [...new Set([primary, ...fallbackModels].filter(Boolean))];
}

function modelPath(model) {
  return model.startsWith("models/") ? model : `models/${model}`;
}

function apiBase(env) {
  return `${GEMINI_API_HOST}/${getApiVersion(env)}`;
}

function getApiVersion(env) {
  return String(env.GEMINI_API_VERSION || DEFAULT_API_VERSION).trim() || DEFAULT_API_VERSION;
}

function positiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function safeDisplayName(value) {
  return String(value || "smash_result")
    .replace(/[^a-zA-Z0-9._-]/g, "_")
    .slice(0, 80);
}

function stripJsonCodeFence(text) {
  return text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
}

async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jsonResponse(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...corsHeaders(),
      ...headers,
    },
  });
}

function errorStatus(error) {
  if (error instanceof HttpError) {
    return error.status;
  }
  if (error instanceof GeminiApiError) {
    return error.status === 429 ? 429 : 502;
  }
  return 500;
}

function errorPayload(error) {
  if (error instanceof HttpError) {
    return { error: error.message };
  }
  if (error instanceof GeminiApiError) {
    return {
      error: error.message,
      gemini_status: error.status,
      gemini_error: error.payload?.error,
    };
  }
  return {
    error: "Internal proxy error",
    detail: error?.message || String(error),
  };
}

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
  };
}

class HttpError extends Error {
  constructor(status, message, headers = {}) {
    super(message);
    this.status = status;
    this.headers = headers;
  }
}

class GeminiApiError extends Error {
  constructor(status, message, payload) {
    const geminiMessage = payload?.error?.message || payload?.raw || message;
    super(`${message}: ${geminiMessage}`);
    this.status = status;
    this.payload = payload;
  }
}
