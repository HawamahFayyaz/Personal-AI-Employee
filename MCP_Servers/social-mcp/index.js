/**
 * social-mcp — MCP server for Facebook and Instagram (Meta Graph API)
 *
 * Tools:
 *   post_to_facebook(message, image_url?)      → writes to Pending_Approval/
 *   post_to_instagram(message, image_url)      → writes to Pending_Approval/
 *   reply_to_comment(comment_id, message,      → writes to Pending_Approval/
 *                    platform?)                   REQUIRES APPROVAL
 *   get_page_insights(page_id, metrics,        → direct Graph API call (read-only)
 *                     period?)
 *   get_messages(platform, unread_only?)       → direct Graph API call (read-only)
 *
 * Environment variables:
 *   META_PAGE_ACCESS_TOKEN  Long-lived Page Access Token  (required)
 *   META_PAGE_ID            Facebook Page numeric ID       (required)
 *   META_IG_USER_ID         Instagram Business User ID     (optional)
 *   META_API_VERSION        Graph API version              (default: v19.0)
 *   VAULT_ROOT              Override vault root path       (default: auto-detected)
 *   DRY_RUN                 "true" → log without creating files or calling API
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VAULT_ROOT =
  process.env.VAULT_ROOT ?? path.resolve(__dirname, "..", "..");

const PENDING_DIR = path.join(VAULT_ROOT, "Pending_Approval");
const LOGS_DIR    = path.join(VAULT_ROOT, "Logs");

fs.mkdirSync(PENDING_DIR, { recursive: true });
fs.mkdirSync(LOGS_DIR,    { recursive: true });

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const TOKEN   = process.env.META_PAGE_ACCESS_TOKEN ?? "";
const PAGE_ID = process.env.META_PAGE_ID ?? "";
const IG_ID   = process.env.META_IG_USER_ID ?? "";
const API_VER = process.env.META_API_VERSION ?? "v19.0";
const DRY_RUN = (process.env.DRY_RUN ?? "false").trim().toLowerCase() === "true";
const GRAPH   = `https://graph.facebook.com/${API_VER}`;

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------
const today    = new Date().toISOString().slice(0, 10);
const LOG_FILE = path.join(LOGS_DIR, `social_mcp_${today}.log`);

function log(level, ...args) {
  const ts  = new Date().toISOString();
  const msg = `[${ts}] [${level}] ${args.join(" ")}`;
  console.error(msg);
  try {
    fs.appendFileSync(LOG_FILE, msg + "\n", "utf8");
  } catch {}
}

// ---------------------------------------------------------------------------
// Graph API helpers
// ---------------------------------------------------------------------------

/** Perform a GET request against the Graph API and return parsed JSON. */
async function graphGet(endpoint, params = {}) {
  params.access_token = TOKEN;
  const qs  = new URLSearchParams(params).toString();
  const url = `${GRAPH}/${endpoint}?${qs}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  const json = await res.json();
  if (!res.ok) {
    throw new Error(
      `Graph API GET /${endpoint} → HTTP ${res.status}: ${JSON.stringify(json?.error ?? json)}`
    );
  }
  return json;
}

/** Follow cursor-based pagination and collect all items. */
async function graphPaginate(endpoint, params = {}, maxPages = 5) {
  const items = [];
  let page = 0;
  const p = { ...params };
  while (page < maxPages) {
    const data = await graphGet(endpoint, p);
    items.push(...(data.data ?? []));
    const after = data.paging?.cursors?.after;
    if (!data.paging?.next || !after) break;
    p.after = after;
    page++;
  }
  return items;
}

/** Perform a POST request against the Graph API and return parsed JSON. */
async function graphPost(endpoint, payload = {}) {
  payload.access_token = TOKEN;
  const url = `${GRAPH}/${endpoint}`;
  const res = await fetch(url, {
    method:  "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body:    new URLSearchParams(payload).toString(),
  });
  const json = await res.json();
  if (!res.ok) {
    throw new Error(
      `Graph API POST /${endpoint} → HTTP ${res.status}: ${JSON.stringify(json?.error ?? json)}`
    );
  }
  return json;
}

// ---------------------------------------------------------------------------
// Pending-Approval file writer
// ---------------------------------------------------------------------------

/**
 * Write a pending-approval file and return the filename.
 *
 * @param {string} action         Value for the `action` frontmatter field
 * @param {string} title          Human-readable title for the approval file
 * @param {Object} extraFrontmatter  Extra key/value pairs to include in frontmatter
 * @param {string} bodyContent    Markdown body below the frontmatter
 */
function writePendingApproval(action, title, extraFrontmatter, bodyContent) {
  const ts   = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15);
  const safe = action.replace(/[^a-z0-9_]/gi, "_").toUpperCase();
  const filename = `SOCIAL_${safe}_${ts}.md`;
  const dest     = path.join(PENDING_DIR, filename);

  let fm = "---\n";
  fm += `action: ${action}\n`;
  fm += `title: ${title}\n`;
  fm += `created: ${new Date().toISOString()}\n`;
  fm += "status: pending\n";
  fm += "source: social-mcp\n";
  for (const [k, v] of Object.entries(extraFrontmatter)) {
    if (v !== undefined && v !== null && v !== "") {
      fm += `${k}: ${v}\n`;
    }
  }
  fm += "---\n";

  const content = fm + "\n" + bodyContent;

  if (DRY_RUN) {
    log("DRY_RUN", `Would write ${filename}`);
    return filename;
  }

  fs.writeFileSync(dest, content, "utf8");
  log("INFO", `Approval file created → ${filename}`);
  return filename;
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------
const server = new McpServer({
  name:    "social-mcp",
  version: "1.0.0",
});

// ── Tool: post_to_facebook ─────────────────────────────────────────────────
server.tool(
  "post_to_facebook",
  "Draft a Facebook Page post. Writes to Pending_Approval/ for human review before publishing.",
  {
    message:   z.string().min(1).describe("Post text / caption"),
    image_url: z.string().url().optional().describe("Optional image URL to attach"),
  },
  async ({ message, image_url }) => {
    if (!TOKEN) {
      return { content: [{ type: "text", text: "Error: META_PAGE_ACCESS_TOKEN is not set." }] };
    }

    const title = `Facebook post: ${message.slice(0, 60)}${message.length > 60 ? "…" : ""}`;

    const body =
      `## Proposed Facebook Post\n\n${message}\n\n` +
      (image_url ? `**Image:** ${image_url}\n\n` : "") +
      "## To Approve\n" +
      "Move this file to `Approved/` to publish, or `Rejected/` to discard.\n";

    const fm = { platform: "facebook" };
    if (image_url) fm.image_url = image_url;
    fm.message = message;

    const filename = writePendingApproval("facebook_post", title, fm, body);
    log("INFO", `post_to_facebook queued → ${filename}`);

    return {
      content: [
        {
          type: "text",
          text:
            `Post queued for approval.\n\n` +
            `**File:** \`Pending_Approval/${filename}\`\n` +
            `Move it to \`Approved/\` to publish or \`Rejected/\` to discard.`,
        },
      ],
    };
  }
);

// ── Tool: post_to_instagram ────────────────────────────────────────────────
server.tool(
  "post_to_instagram",
  "Draft an Instagram post (image required). Writes to Pending_Approval/ for human review before publishing.",
  {
    message:   z.string().min(1).describe("Caption text"),
    image_url: z.string().url().describe("Publicly accessible image URL (required for Instagram)"),
  },
  async ({ message, image_url }) => {
    if (!TOKEN) {
      return { content: [{ type: "text", text: "Error: META_PAGE_ACCESS_TOKEN is not set." }] };
    }
    if (!IG_ID) {
      return { content: [{ type: "text", text: "Error: META_IG_USER_ID is not set." }] };
    }

    const title = `Instagram post: ${message.slice(0, 60)}${message.length > 60 ? "…" : ""}`;

    const body =
      `## Proposed Instagram Post\n\n**Caption:**\n${message}\n\n` +
      `**Image:** ${image_url}\n\n` +
      "## To Approve\n" +
      "Move this file to `Approved/` to publish, or `Rejected/` to discard.\n";

    const fm = {
      platform:  "instagram",
      message,
      image_url,
    };

    const filename = writePendingApproval("instagram_post", title, fm, body);
    log("INFO", `post_to_instagram queued → ${filename}`);

    return {
      content: [
        {
          type: "text",
          text:
            `Instagram post queued for approval.\n\n` +
            `**File:** \`Pending_Approval/${filename}\`\n` +
            `Move it to \`Approved/\` to publish or \`Rejected/\` to discard.`,
        },
      ],
    };
  }
);

// ── Tool: reply_to_comment ─────────────────────────────────────────────────
server.tool(
  "reply_to_comment",
  "Draft a reply to a Facebook or Instagram comment. REQUIRES APPROVAL — writes to Pending_Approval/.",
  {
    comment_id: z.string().min(1).describe("The Graph API comment ID to reply to"),
    message:    z.string().min(1).describe("Reply text"),
    platform:   z
      .enum(["facebook", "instagram"])
      .default("facebook")
      .describe("Platform of the comment"),
  },
  async ({ comment_id, message, platform }) => {
    if (!TOKEN) {
      return { content: [{ type: "text", text: "Error: META_PAGE_ACCESS_TOKEN is not set." }] };
    }

    const title = `Reply to ${platform} comment ${comment_id}`;

    const body =
      `## Proposed Reply\n\n` +
      `**Platform:** ${platform}\n` +
      `**Comment ID:** \`${comment_id}\`\n\n` +
      `**Reply Text:**\n${message}\n\n` +
      "## To Approve\n" +
      "Move this file to `Approved/` to post the reply, or `Rejected/` to discard.\n";

    const fm = {
      platform,
      comment_id,
      message,
    };

    const filename = writePendingApproval("social_reply", title, fm, body);
    log("INFO", `reply_to_comment queued → ${filename} (comment=${comment_id})`);

    return {
      content: [
        {
          type: "text",
          text:
            `⚠️ Reply requires approval.\n\n` +
            `**File:** \`Pending_Approval/${filename}\`\n` +
            `**Platform:** ${platform}  |  **Comment ID:** \`${comment_id}\`\n\n` +
            `Move the file to \`Approved/\` to post or \`Rejected/\` to discard.`,
        },
      ],
    };
  }
);

// ── Tool: get_page_insights ────────────────────────────────────────────────
server.tool(
  "get_page_insights",
  "Fetch Facebook Page or Instagram account insights (reach, impressions, engagement, etc.).",
  {
    page_id: z
      .string()
      .optional()
      .describe("Page or IG user ID to query (defaults to META_PAGE_ID)"),
    metrics: z
      .string()
      .describe(
        "Comma-separated metrics, e.g. 'page_impressions,page_engaged_users' or 'impressions,reach'"
      ),
    period: z
      .enum(["day", "week", "days_28", "month", "lifetime"])
      .default("day")
      .describe("Aggregation period"),
  },
  async ({ page_id, metrics, period }) => {
    if (!TOKEN) {
      return { content: [{ type: "text", text: "Error: META_PAGE_ACCESS_TOKEN is not set." }] };
    }

    const target = page_id || PAGE_ID;
    if (!target) {
      return { content: [{ type: "text", text: "Error: provide page_id or set META_PAGE_ID." }] };
    }

    try {
      const data = await graphGet(`${target}/insights`, {
        metric: metrics,
        period,
      });

      const rows = (data.data ?? []).map((item) => {
        const latest = item.values?.[item.values.length - 1] ?? {};
        return `- **${item.name}** (${item.period}): ${JSON.stringify(latest.value ?? "n/a")}  *(as of ${latest.end_time ?? "?"})*`;
      });

      const text =
        rows.length > 0
          ? `## Insights for \`${target}\` (period: ${period})\n\n${rows.join("\n")}`
          : `No insights data returned for \`${target}\` with metrics: ${metrics}`;

      return { content: [{ type: "text", text }] };
    } catch (err) {
      log("ERROR", "get_page_insights:", err.message);
      return { content: [{ type: "text", text: `Error fetching insights: ${err.message}` }] };
    }
  }
);

// ── Tool: get_messages ─────────────────────────────────────────────────────
server.tool(
  "get_messages",
  "Fetch Messenger or Instagram DM conversations for the Page.",
  {
    platform: z
      .enum(["facebook", "instagram"])
      .default("facebook")
      .describe("Which inbox to query"),
    unread_only: z
      .boolean()
      .default(false)
      .describe("If true, return only threads with unread_count > 0"),
    limit: z
      .number()
      .int()
      .min(1)
      .max(25)
      .default(10)
      .describe("Max number of conversation threads to return"),
  },
  async ({ platform, unread_only, limit }) => {
    if (!TOKEN) {
      return { content: [{ type: "text", text: "Error: META_PAGE_ACCESS_TOKEN is not set." }] };
    }

    const userId = platform === "instagram" ? IG_ID : PAGE_ID;
    if (!userId) {
      const envVar = platform === "instagram" ? "META_IG_USER_ID" : "META_PAGE_ID";
      return { content: [{ type: "text", text: `Error: ${envVar} is not set.` }] };
    }

    try {
      const threads = await graphPaginate(
        `${userId}/conversations`,
        {
          platform,
          fields: "id,participants,messages{id,message,from,created_time},unread_count,updated_time",
          limit:  String(limit),
        },
        2  // max 2 pages to stay within rate limits
      );

      const filtered = unread_only
        ? threads.filter((t) => (t.unread_count ?? 0) > 0)
        : threads;

      if (filtered.length === 0) {
        return {
          content: [
            {
              type: "text",
              text: unread_only
                ? `No unread ${platform} messages found.`
                : `No ${platform} message threads found.`,
            },
          ],
        };
      }

      const lines = filtered.flatMap((thread) => {
        const participants = (thread.participants?.data ?? [])
          .map((p) => p.name ?? p.username ?? p.id)
          .join(", ");
        const unread = thread.unread_count ?? 0;
        const msgs   = thread.messages?.data ?? [];
        const latest = msgs[0] ?? {};
        const header = `### Thread \`${thread.id}\`  (unread: ${unread})`;
        const meta   = `**Participants:** ${participants}  |  **Updated:** ${thread.updated_time ?? "?"}`;
        const preview = latest.message
          ? `> ${latest.message.slice(0, 120)}${latest.message.length > 120 ? "…" : ""}`
          : "_No message preview_";
        return [header, meta, preview, ""];
      });

      return {
        content: [
          {
            type: "text",
            text:
              `## ${platform.charAt(0).toUpperCase() + platform.slice(1)} Messages` +
              ` (${filtered.length} thread${filtered.length !== 1 ? "s" : ""})\n\n` +
              lines.join("\n"),
          },
        ],
      };
    } catch (err) {
      log("ERROR", "get_messages:", err.message);
      return {
        content: [{ type: "text", text: `Error fetching messages: ${err.message}` }],
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Startup validation
// ---------------------------------------------------------------------------
function validateConfig() {
  const warnings = [];
  if (!TOKEN)   warnings.push("META_PAGE_ACCESS_TOKEN is not set — all tools will fail.");
  if (!PAGE_ID) warnings.push("META_PAGE_ID is not set — Facebook tools will fail.");
  if (!IG_ID)   warnings.push("META_IG_USER_ID is not set — Instagram tools will fail.");
  if (DRY_RUN)  warnings.push("DRY_RUN=true — approval files will be logged but not written.");
  for (const w of warnings) log("WARN", w);
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
validateConfig();

const transport = new StdioServerTransport();
await server.connect(transport);
log("INFO", `social-mcp started (vault=${VAULT_ROOT}, api=${API_VER}, dry_run=${DRY_RUN})`);
