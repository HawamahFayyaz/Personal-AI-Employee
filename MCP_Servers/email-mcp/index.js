/**
 * email-mcp — MCP server for Gmail (send, draft, search)
 *
 * Tools exposed:
 *   send_email(to, subject, body, cc?, bcc?)
 *   draft_email(to, subject, body)
 *   search_emails(query, max_results?)
 *
 * Environment variables:
 *   GMAIL_CREDENTIALS  path to credentials.json  (default: <vault>/credentials.json)
 *   GMAIL_TOKEN        path to token.json         (default: <vault>/token.json)
 *   DRY_RUN            "true" → log instead of calling Gmail API
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { google } from "googleapis";
import { OAuth2Client } from "google-auth-library";
import { z } from "zod";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VAULT_ROOT = path.resolve(__dirname, "..", "..");

const CREDENTIALS_PATH =
  process.env.GMAIL_CREDENTIALS ?? path.join(VAULT_ROOT, "credentials.json");
const TOKEN_PATH =
  process.env.GMAIL_TOKEN ?? path.join(VAULT_ROOT, "token.json");

const DRY_RUN =
  (process.env.DRY_RUN ?? "false").trim().toLowerCase() === "true";

// Drafts folder inside vault (for draft_email tool)
const DRAFTS_DIR = path.join(VAULT_ROOT, "Drafts");
fs.mkdirSync(DRAFTS_DIR, { recursive: true });

// ---------------------------------------------------------------------------
// Gmail auth — loads existing token.json (same creds as gmail_auth.py)
// ---------------------------------------------------------------------------
function buildGmailClient() {
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    throw new Error(
      `credentials.json not found at ${CREDENTIALS_PATH}. ` +
        "Set GMAIL_CREDENTIALS env var or place the file in the vault root."
    );
  }
  if (!fs.existsSync(TOKEN_PATH)) {
    throw new Error(
      `token.json not found at ${TOKEN_PATH}. ` +
        "Run the Python gmail_auth.py flow first to generate this file."
    );
  }

  const credentials = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, "utf8"));
  const { client_id, client_secret, redirect_uris } =
    credentials.installed ?? credentials.web;

  const oAuth2Client = new OAuth2Client(
    client_id,
    client_secret,
    redirect_uris[0]
  );

  const token = JSON.parse(fs.readFileSync(TOKEN_PATH, "utf8"));
  oAuth2Client.setCredentials(token);

  // Auto-save refreshed tokens back to token.json
  oAuth2Client.on("tokens", (newTokens) => {
    const existing = JSON.parse(fs.readFileSync(TOKEN_PATH, "utf8"));
    const merged = { ...existing, ...newTokens };
    fs.writeFileSync(TOKEN_PATH, JSON.stringify(merged, null, 2));
  });

  return google.gmail({ version: "v1", auth: oAuth2Client });
}

// ---------------------------------------------------------------------------
// RFC 2822 email builder → base64url for Gmail API
// ---------------------------------------------------------------------------
function buildRawMessage({ to, subject, body, cc, bcc, from }) {
  const headers = [
    `From: ${from ?? "me"}`,
    `To: ${Array.isArray(to) ? to.join(", ") : to}`,
    `Subject: ${subject}`,
    `MIME-Version: 1.0`,
    `Content-Type: text/plain; charset="UTF-8"`,
    `Content-Transfer-Encoding: 7bit`,
  ];
  if (cc) headers.push(`Cc: ${Array.isArray(cc) ? cc.join(", ") : cc}`);
  if (bcc) headers.push(`Bcc: ${Array.isArray(bcc) ? bcc.join(", ") : bcc}`);

  const message = [...headers, "", body].join("\r\n");
  return Buffer.from(message)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

// ---------------------------------------------------------------------------
// Logging helper (stderr so it doesn't corrupt the MCP stdio stream)
// ---------------------------------------------------------------------------
function log(label, detail) {
  const ts = new Date().toISOString();
  process.stderr.write(`[${ts}] [email-mcp] ${label}${detail ? ": " + detail : ""}\n`);
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

async function toolSendEmail({ to, subject, body, cc, bcc }) {
  const recipients = Array.isArray(to) ? to : [to];

  if (DRY_RUN) {
    const summary = JSON.stringify({ to: recipients, subject, cc, bcc }, null, 2);
    log("DRY_RUN send_email", summary);
    log("DRY_RUN body", body);
    return {
      success: true,
      dry_run: true,
      message: `[DRY RUN] Would send email to ${recipients.join(", ")}: "${subject}"`,
    };
  }

  const gmail = buildGmailClient();
  const raw = buildRawMessage({ to: recipients, subject, body, cc, bcc });

  const res = await gmail.users.messages.send({
    userId: "me",
    requestBody: { raw },
  });

  log("send_email sent", `id=${res.data.id}, to=${recipients.join(", ")}`);
  return {
    success: true,
    message_id: res.data.id,
    thread_id: res.data.threadId,
    message: `Email sent successfully to ${recipients.join(", ")}`,
  };
}

async function toolDraftEmail({ to, subject, body }) {
  const recipients = Array.isArray(to) ? to : [to];

  // Always save a local markdown draft as a record
  const now = new Date();
  const slug = now
    .toISOString()
    .replace(/[:.]/g, "-")
    .slice(0, 19);
  const filename = `DRAFT_${slug}_${subject.replace(/[^a-zA-Z0-9 _-]/g, "_").slice(0, 40)}.md`;
  const localPath = path.join(DRAFTS_DIR, filename);

  const localContent = [
    "---",
    "type: email_draft",
    `to: ${recipients.join(", ")}`,
    `subject: ${subject}`,
    `created: ${now.toISOString()}`,
    "status: draft",
    "---",
    "",
    "## Body",
    "",
    body,
  ].join("\n");

  fs.writeFileSync(localPath, localContent, "utf8");
  log("draft_email local", filename);

  if (DRY_RUN) {
    return {
      success: true,
      dry_run: true,
      local_draft: localPath,
      message: `[DRY RUN] Draft saved locally to ${filename}`,
    };
  }

  // Also create a Gmail draft
  const gmail = buildGmailClient();
  const raw = buildRawMessage({ to: recipients, subject, body });

  const res = await gmail.users.drafts.create({
    userId: "me",
    requestBody: { message: { raw } },
  });

  log("draft_email gmail", `draftId=${res.data.id}`);
  return {
    success: true,
    draft_id: res.data.id,
    local_draft: localPath,
    message: `Draft saved — Gmail draft ID: ${res.data.id}, local copy: ${filename}`,
  };
}

async function toolSearchEmails({ query, max_results = 10 }) {
  if (DRY_RUN) {
    log("DRY_RUN search_emails", `query="${query}", max=${max_results}`);
    return {
      success: true,
      dry_run: true,
      results: [],
      message: `[DRY RUN] Would search Gmail for: "${query}"`,
    };
  }

  const gmail = buildGmailClient();

  const listRes = await gmail.users.messages.list({
    userId: "me",
    q: query,
    maxResults: Math.min(Number(max_results), 50),
  });

  const messages = listRes.data.messages ?? [];
  if (messages.length === 0) {
    return { success: true, results: [], message: "No messages found." };
  }

  // Fetch metadata for each message in parallel
  const details = await Promise.all(
    messages.map((m) =>
      gmail.users.messages
        .get({ userId: "me", id: m.id, format: "metadata",
               metadataHeaders: ["From", "To", "Subject", "Date"] })
        .then((r) => {
          const headers = r.data.payload?.headers ?? [];
          const hdr = (name) =>
            headers.find((h) => h.name.toLowerCase() === name.toLowerCase())
              ?.value ?? "";
          return {
            id: r.data.id,
            thread_id: r.data.threadId,
            from: hdr("From"),
            to: hdr("To"),
            subject: hdr("Subject"),
            date: hdr("Date"),
            snippet: r.data.snippet ?? "",
          };
        })
    )
  );

  log("search_emails", `found ${details.length} results for "${query}"`);
  return {
    success: true,
    count: details.length,
    results: details,
    message: `Found ${details.length} message(s) matching "${query}"`,
  };
}

// ---------------------------------------------------------------------------
// MCP Server setup
// ---------------------------------------------------------------------------
const server = new McpServer({
  name: "email",
  version: "1.0.0",
});

// --- send_email ---
server.tool(
  "send_email",
  "Send an email via Gmail. In DRY_RUN mode logs the email without sending.",
  {
    to: z.string().describe("Recipient email address (or comma-separated list)."),
    subject: z.string().describe("Email subject line."),
    body: z.string().describe("Plain-text email body."),
    cc: z.string().optional().describe("CC recipients (comma-separated)."),
    bcc: z.string().optional().describe("BCC recipients (comma-separated)."),
  },
  async (args) => {
    try {
      const result = await toolSendEmail(args);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      log("send_email ERROR", err.message);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ success: false, error: err.message }, null, 2),
          },
        ],
        isError: true,
      };
    }
  }
);

// --- draft_email ---
server.tool(
  "draft_email",
  "Save an email as a draft (locally in Drafts/ and in Gmail). Does not send.",
  {
    to: z.string().describe("Recipient email address (or comma-separated list)."),
    subject: z.string().describe("Email subject line."),
    body: z.string().describe("Plain-text email body."),
  },
  async (args) => {
    try {
      const result = await toolDraftEmail(args);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      log("draft_email ERROR", err.message);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ success: false, error: err.message }, null, 2),
          },
        ],
        isError: true,
      };
    }
  }
);

// --- search_emails ---
server.tool(
  "search_emails",
  "Search Gmail using a query string (same syntax as Gmail search bar).",
  {
    query: z
      .string()
      .describe('Gmail search query, e.g. "from:alice@example.com subject:invoice".'),
    max_results: z
      .number()
      .int()
      .min(1)
      .max(50)
      .optional()
      .describe("Maximum number of results to return (default 10, max 50)."),
  },
  async (args) => {
    try {
      const result = await toolSearchEmails(args);
      return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      log("search_emails ERROR", err.message);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ success: false, error: err.message }, null, 2),
          },
        ],
        isError: true,
      };
    }
  }
);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
log("startup", `DRY_RUN=${DRY_RUN}, credentials=${CREDENTIALS_PATH}, token=${TOKEN_PATH}`);

const transport = new StdioServerTransport();
await server.connect(transport);
