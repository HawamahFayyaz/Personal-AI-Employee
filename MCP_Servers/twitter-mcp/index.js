/**
 * twitter-mcp — MCP server for Twitter/X API v2
 *
 * Tools:
 *   post_tweet(text, media_ids?)             → Pending_Approval/ (requires approval)
 *   reply_to_tweet(tweet_id, text)           → Pending_Approval/ (REQUIRES APPROVAL)
 *   get_mentions(since_id?, max_results?)    → direct API call (read-only)
 *   get_timeline(count?)                     → direct API call (read-only)
 *   get_analytics(tweet_id)                  → direct API call (read-only)
 *
 * Environment variables:
 *   TWITTER_BEARER_TOKEN    App-only Bearer Token (read-only fallback)
 *   TWITTER_CLIENT_ID       OAuth 2.0 Client ID (required for token refresh)
 *   TWITTER_CLIENT_SECRET   Client Secret (confidential apps; omit for public)
 *   TWITTER_USER_ID         Authenticated user's numeric ID
 *   VAULT_ROOT              Override vault root (default: auto-detected)
 *   DRY_RUN                 "true" → log without writing files or calling write APIs
 *
 * OAuth tokens are read from {vault_root}/.twitter_token.json.
 */

import { McpServer }           from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z }                   from "zod";
import fs                      from "fs";
import path                    from "path";
import { fileURLToPath }       from "url";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------
const __dirname  = path.dirname(fileURLToPath(import.meta.url));
const VAULT_ROOT = process.env.VAULT_ROOT ?? path.resolve(__dirname, "..", "..");

const PENDING_DIR  = path.join(VAULT_ROOT, "Pending_Approval");
const LOGS_DIR     = path.join(VAULT_ROOT, "Logs");
const TOKEN_FILE   = path.join(VAULT_ROOT, ".twitter_token.json");

fs.mkdirSync(PENDING_DIR, { recursive: true });
fs.mkdirSync(LOGS_DIR,    { recursive: true });

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const BEARER_TOKEN   = process.env.TWITTER_BEARER_TOKEN ?? "";
const CLIENT_ID      = process.env.TWITTER_CLIENT_ID    ?? "";
const CLIENT_SECRET  = process.env.TWITTER_CLIENT_SECRET ?? "";
const USER_ID        = process.env.TWITTER_USER_ID       ?? "";
const DRY_RUN        = (process.env.DRY_RUN ?? "false").trim().toLowerCase() === "true";
const API_BASE       = "https://api.twitter.com/2";

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
const today    = new Date().toISOString().slice(0, 10);
const LOG_FILE = path.join(LOGS_DIR, `twitter_mcp_${today}.log`);

function log(level, ...args) {
  const ts  = new Date().toISOString();
  const msg = `[${ts}] [${level}] ${args.join(" ")}`;
  console.error(msg);
  try { fs.appendFileSync(LOG_FILE, msg + "\n", "utf8"); } catch {}
}

// ---------------------------------------------------------------------------
// Token management
// ---------------------------------------------------------------------------

function loadTokens() {
  if (!fs.existsSync(TOKEN_FILE)) return {};
  try { return JSON.parse(fs.readFileSync(TOKEN_FILE, "utf8")); }
  catch { return {}; }
}

function saveTokens(data) {
  fs.writeFileSync(TOKEN_FILE, JSON.stringify(data, null, 2), "utf8");
}

async function refreshAccessToken(tokens) {
  if (!CLIENT_ID || !tokens.refresh_token) {
    throw new Error("Cannot refresh: TWITTER_CLIENT_ID or refresh_token missing.");
  }

  const params = new URLSearchParams({
    grant_type:    "refresh_token",
    refresh_token: tokens.refresh_token,
    client_id:     CLIENT_ID,
  });

  const headers = { "Content-Type": "application/x-www-form-urlencoded" };
  if (CLIENT_SECRET) {
    const creds = Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64");
    headers["Authorization"] = `Basic ${creds}`;
  }

  const res  = await fetch("https://api.twitter.com/2/oauth2/token", {
    method:  "POST",
    headers,
    body:    params.toString(),
  });
  const json = await res.json();
  if (!res.ok) {
    throw new Error(`Token refresh failed: ${JSON.stringify(json)}`);
  }

  const merged = {
    ...tokens,
    ...json,
    expires_at: Math.floor(Date.now() / 1000) + (json.expires_in ?? 7200) - 60,
  };
  saveTokens(merged);
  log("INFO", "Access token refreshed successfully.");
  return merged;
}

/**
 * Return a valid Authorization header string.
 * Prefers OAuth 2.0 user context over app-only Bearer.
 */
async function getAuthHeader() {
  let tokens = loadTokens();
  if (tokens.access_token) {
    const now = Math.floor(Date.now() / 1000);
    if (now >= (tokens.expires_at ?? 0)) {
      log("INFO", "Access token expired — refreshing…");
      tokens = await refreshAccessToken(tokens);
    }
    return `Bearer ${tokens.access_token}`;
  }
  if (BEARER_TOKEN) return `Bearer ${BEARER_TOKEN}`;
  throw new Error(
    "No Twitter auth available. Run twitter_auth.py or set TWITTER_BEARER_TOKEN."
  );
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function twitterGet(endpoint, params = {}) {
  const qs  = new URLSearchParams(params).toString();
  const url = `${API_BASE}/${endpoint}${qs ? "?" + qs : ""}`;
  const auth = await getAuthHeader();

  const res  = await fetch(url, { headers: { Authorization: auth } });
  const json = await res.json();

  if (!res.ok) {
    const detail = json?.errors?.[0]?.detail ?? json?.title ?? JSON.stringify(json);
    throw new Error(`Twitter GET /${endpoint} → HTTP ${res.status}: ${detail}`);
  }
  return json;
}

async function twitterPost(endpoint, payload) {
  const url  = `${API_BASE}/${endpoint}`;
  const auth = await getAuthHeader();

  const res  = await fetch(url, {
    method:  "POST",
    headers: { Authorization: auth, "Content-Type": "application/json" },
    body:    JSON.stringify(payload),
  });
  const json = await res.json();

  if (!res.ok) {
    const detail = json?.errors?.[0]?.detail ?? json?.title ?? JSON.stringify(json);
    throw new Error(`Twitter POST /${endpoint} → HTTP ${res.status}: ${detail}`);
  }
  return json;
}

// ---------------------------------------------------------------------------
// Pending-Approval writer
// ---------------------------------------------------------------------------

function writePendingApproval(action, title, extraFm, bodyContent) {
  const ts       = new Date().toISOString().replace(/[-:T.Z]/g, "").slice(0, 15);
  const safe     = action.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
  const filename = `TWITTER_${safe}_${ts}.md`;
  const dest     = path.join(PENDING_DIR, filename);

  let fm = "---\n";
  fm += `action: ${action}\n`;
  fm += `title: ${title}\n`;
  fm += `created: ${new Date().toISOString()}\n`;
  fm += "status: pending\n";
  fm += "source: twitter-mcp\n";
  for (const [k, v] of Object.entries(extraFm)) {
    if (v !== undefined && v !== null && v !== "") fm += `${k}: ${v}\n`;
  }
  fm += "---\n";

  if (DRY_RUN) {
    log("DRY_RUN", `Would write ${filename}`);
    return filename;
  }
  fs.writeFileSync(dest, fm + "\n" + bodyContent, "utf8");
  log("INFO", `Approval file created → ${filename}`);
  return filename;
}

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function buildAuthorMap(includes) {
  const users = includes?.users ?? [];
  return Object.fromEntries(users.map((u) => [u.id, u]));
}

function formatTweet(tweet, authorMap) {
  const author   = authorMap[tweet.author_id] ?? {};
  const name     = author.name     ?? "Unknown";
  const username = author.username ?? "unknown";
  const m        = tweet.public_metrics ?? {};
  return (
    `**@${username}** (${name}) · ${tweet.created_at ?? "?"}\n` +
    `> ${tweet.text}\n` +
    `*${m.like_count ?? 0} likes · ${m.retweet_count ?? 0} RTs · ` +
    `${m.reply_count ?? 0} replies · ID: \`${tweet.id}\`*`
  );
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------
const server = new McpServer({ name: "twitter-mcp", version: "1.0.0" });

// ── Tool: post_tweet ────────────────────────────────────────────────────────
server.tool(
  "post_tweet",
  "Draft a tweet. Writes to Pending_Approval/ for human review before posting.",
  {
    text: z
      .string()
      .min(1)
      .max(280)
      .describe("Tweet text (max 280 characters)"),
    media_ids: z
      .string()
      .optional()
      .describe("Comma-separated media IDs from the Twitter v1.1 media upload endpoint"),
  },
  async ({ text, media_ids }) => {
    const charCount = [...text].length;   // Unicode-aware
    if (charCount > 280) {
      return {
        content: [{
          type: "text",
          text: `Error: tweet is ${charCount} characters (max 280). Please shorten it.`,
        }],
      };
    }

    const title    = `Tweet: ${text.slice(0, 60)}${text.length > 60 ? "…" : ""}`;
    const fm: Record<string, string> = { text };
    if (media_ids) fm.media_ids = media_ids;

    const body =
      `## Proposed Tweet\n\n${text}\n\n` +
      (media_ids ? `**Media IDs:** ${media_ids}\n\n` : "") +
      `**Characters:** ${charCount} / 280\n\n` +
      "## To Approve\n" +
      "Move this file to `Approved/` to post, or `Rejected/` to discard.\n";

    const filename = writePendingApproval("tweet", title, fm, body);

    return {
      content: [{
        type: "text",
        text:
          `Tweet queued for approval.\n\n` +
          `**File:** \`Pending_Approval/${filename}\`\n` +
          `**Length:** ${charCount}/280 characters\n\n` +
          "Move to `Approved/` to post or `Rejected/` to discard.",
      }],
    };
  }
);

// ── Tool: reply_to_tweet ────────────────────────────────────────────────────
server.tool(
  "reply_to_tweet",
  "Draft a reply to a tweet. REQUIRES APPROVAL — writes to Pending_Approval/.",
  {
    tweet_id: z.string().min(1).describe("ID of the tweet to reply to"),
    text:     z.string().min(1).max(280).describe("Reply text (max 280 characters)"),
  },
  async ({ tweet_id, text }) => {
    const charCount = [...text].length;
    if (charCount > 280) {
      return {
        content: [{
          type: "text",
          text: `Error: reply is ${charCount} characters (max 280).`,
        }],
      };
    }

    // Fetch the original tweet for context
    let originalText = "_Could not fetch original tweet_";
    try {
      const orig = await twitterGet(`tweets/${tweet_id}`, {
        "tweet.fields": "text,created_at,author_id",
        "expansions":   "author_id",
        "user.fields":  "name,username",
      });
      const author = buildAuthorMap(orig.includes)[orig.data?.author_id ?? ""] ?? {};
      originalText =
        `@${author.username ?? "?"}: ${orig.data?.text ?? ""}`;
    } catch (err) {
      log("WARN", "Could not fetch original tweet:", err.message);
    }

    const title = `Reply to tweet ${tweet_id}: ${text.slice(0, 40)}…`;
    const fm    = { in_reply_to_tweet_id: tweet_id, text };

    const body =
      `## Original Tweet\n\n> ${originalText}\n\n` +
      `## Proposed Reply\n\n${text}\n\n` +
      `**Characters:** ${charCount} / 280\n\n` +
      "## To Approve\n" +
      "Move this file to `Approved/` to post the reply, or `Rejected/` to discard.\n";

    const filename = writePendingApproval("tweet_reply", title, fm, body);

    return {
      content: [{
        type: "text",
        text:
          `⚠️ Reply requires approval.\n\n` +
          `**File:** \`Pending_Approval/${filename}\`\n` +
          `**Replying to:** \`${tweet_id}\`\n\n` +
          "Move to `Approved/` to post or `Rejected/` to discard.",
      }],
    };
  }
);

// ── Tool: get_mentions ──────────────────────────────────────────────────────
server.tool(
  "get_mentions",
  "Fetch recent mentions of the authenticated account.",
  {
    since_id: z
      .string()
      .optional()
      .describe("Return only tweets newer than this tweet ID (for incremental fetching)"),
    max_results: z
      .number()
      .int()
      .min(1)
      .max(100)
      .default(20)
      .describe("Number of mentions to return (1-100, default 20)"),
  },
  async ({ since_id, max_results }) => {
    const uid = USER_ID;
    if (!uid) {
      return {
        content: [{ type: "text", text: "Error: TWITTER_USER_ID is not set." }],
      };
    }

    try {
      const params: Record<string, string> = {
        "tweet.fields": "id,text,created_at,author_id,conversation_id,public_metrics",
        "expansions":   "author_id",
        "user.fields":  "name,username,verified",
        "max_results":  String(max_results),
      };
      if (since_id) params.since_id = since_id;

      const resp    = await twitterGet(`users/${uid}/mentions`, params);
      const tweets  = resp.data ?? [];

      if (tweets.length === 0) {
        return { content: [{ type: "text", text: "No mentions found." }] };
      }

      const authorMap = buildAuthorMap(resp.includes);
      const newest    = tweets[0].id;
      const lines     = tweets.map((t) => formatTweet(t, authorMap));

      return {
        content: [{
          type: "text",
          text:
            `## Mentions (${tweets.length} tweet${tweets.length !== 1 ? "s" : ""})\n` +
            `*Newest ID: \`${newest}\` — use as \`since_id\` next time*\n\n` +
            lines.join("\n\n---\n\n"),
        }],
      };
    } catch (err) {
      log("ERROR", "get_mentions:", err.message);
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }
);

// ── Tool: get_timeline ──────────────────────────────────────────────────────
server.tool(
  "get_timeline",
  "Fetch the authenticated account's reverse-chronological home timeline.",
  {
    count: z
      .number()
      .int()
      .min(1)
      .max(100)
      .default(20)
      .describe("Number of tweets to return (1-100, default 20)"),
  },
  async ({ count }) => {
    const uid = USER_ID;
    if (!uid) {
      return {
        content: [{ type: "text", text: "Error: TWITTER_USER_ID is not set." }],
      };
    }

    try {
      const resp = await twitterGet(
        `users/${uid}/timelines/reverse_chronological`,
        {
          "tweet.fields": "id,text,created_at,author_id,public_metrics",
          "expansions":   "author_id",
          "user.fields":  "name,username",
          "max_results":  String(count),
        }
      );
      const tweets = resp.data ?? [];

      if (tweets.length === 0) {
        return { content: [{ type: "text", text: "Timeline is empty." }] };
      }

      const authorMap = buildAuthorMap(resp.includes);
      const lines     = tweets.map((t) => formatTweet(t, authorMap));

      return {
        content: [{
          type: "text",
          text:
            `## Home Timeline (${tweets.length} tweet${tweets.length !== 1 ? "s" : ""})\n\n` +
            lines.join("\n\n---\n\n"),
        }],
      };
    } catch (err) {
      log("ERROR", "get_timeline:", err.message);
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }
);

// ── Tool: get_analytics ─────────────────────────────────────────────────────
server.tool(
  "get_analytics",
  "Fetch public engagement metrics for a specific tweet.",
  {
    tweet_id: z.string().min(1).describe("The tweet ID to analyse"),
  },
  async ({ tweet_id }) => {
    try {
      const resp = await twitterGet(`tweets/${tweet_id}`, {
        "tweet.fields": "id,text,created_at,author_id,public_metrics,organic_metrics",
        "expansions":   "author_id",
        "user.fields":  "name,username",
      });

      const tweet  = resp.data;
      if (!tweet) {
        return { content: [{ type: "text", text: `Tweet \`${tweet_id}\` not found.` }] };
      }

      const authorMap = buildAuthorMap(resp.includes);
      const author    = authorMap[tweet.author_id] ?? {};
      const pub       = tweet.public_metrics     ?? {};
      const org       = tweet.organic_metrics    ?? {};   // only for own tweets with user auth

      const lines = [
        `## Analytics for Tweet \`${tweet_id}\``,
        "",
        `**Author:** @${author.username ?? "?"} (${author.name ?? "?"})`,
        `**Posted:** ${tweet.created_at ?? "?"}`,
        "",
        `> ${tweet.text}`,
        "",
        "### Public Metrics",
        `| Metric | Count |`,
        `|--------|-------|`,
        `| Likes | ${pub.like_count ?? "n/a"} |`,
        `| Retweets | ${pub.retweet_count ?? "n/a"} |`,
        `| Replies | ${pub.reply_count ?? "n/a"} |`,
        `| Quotes | ${pub.quote_count ?? "n/a"} |`,
        `| Bookmarks | ${pub.bookmark_count ?? "n/a"} |`,
        `| Impressions | ${pub.impression_count ?? "n/a"} |`,
      ];

      if (Object.keys(org).length > 0) {
        lines.push("", "### Organic Metrics *(own tweets only)*");
        lines.push(`| Metric | Count |`, `|--------|-------|`);
        for (const [k, v] of Object.entries(org)) {
          lines.push(`| ${k.replace(/_/g, " ")} | ${v} |`);
        }
      }

      return { content: [{ type: "text", text: lines.join("\n") }] };
    } catch (err) {
      log("ERROR", "get_analytics:", err.message);
      return { content: [{ type: "text", text: `Error: ${err.message}` }] };
    }
  }
);

// ---------------------------------------------------------------------------
// Startup checks
// ---------------------------------------------------------------------------
function validateConfig() {
  if (!BEARER_TOKEN && !fs.existsSync(TOKEN_FILE)) {
    log("WARN", "No TWITTER_BEARER_TOKEN and no .twitter_token.json — all tools will fail.");
    log("WARN", "Run: python Watchers/twitter_auth.py");
  }
  if (!USER_ID) {
    log("WARN", "TWITTER_USER_ID not set — mentions and timeline tools will fail.");
  }
  if (DRY_RUN) {
    log("WARN", "DRY_RUN=true — approval files will not be written.");
  }
}

validateConfig();
const transport = new StdioServerTransport();
await server.connect(transport);
log("INFO", `twitter-mcp started (vault=${VAULT_ROOT}, user_id=${USER_ID || "NOT SET"}, dry_run=${DRY_RUN})`);
