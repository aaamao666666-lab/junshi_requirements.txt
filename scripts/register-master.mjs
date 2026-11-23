import { readFile } from "node:fs/promises";

function readVars(source) {
  const out = {};
  for (const line of source.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$/);
    if (match && !match[1].startsWith("#")) out[match[1]] = match[2].replace(/^["']|["']$/g, "");
  }
  return out;
}
const vars = readVars(await readFile(".dev.vars", "utf8"));
const token = vars.MASTER_BOT_TOKEN || process.env.MASTER_BOT_TOKEN;
const secret = vars.MASTER_WEBHOOK_SECRET || process.env.MASTER_WEBHOOK_SECRET;
const base = (vars.PUBLIC_WORKER_URL || process.env.PUBLIC_WORKER_URL || "").replace(/\/$/, "");
if (!token || !secret || !base) throw new Error("Set MASTER_BOT_TOKEN, MASTER_WEBHOOK_SECRET and PUBLIC_WORKER_URL in .dev.vars");
const call = async (method, payload) => {
  const response = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload)
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(`${method}: ${result.description || response.status}`);
  return result.result;
};
const me = await call("getMe", {});
await call("setWebhook", {
  url: `${base}/webhook/master`,
  secret_token: secret,
  allowed_updates: ["message", "callback_query"]
});
await call("setMyCommands", [
  { command: "start", description: "打开 KK 总后台" },
  { command: "bots", description: "查看已绑定子机器人" }
]);
console.log(`Master webhook registered for @${me.username} at ${base}/webhook/master`);
