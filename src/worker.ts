interface Env {
  DB: D1Database;
  MASTER_BOT_TOKEN: string;
  MASTER_WEBHOOK_SECRET: string;
  TOKEN_ENCRYPTION_KEY: string;
  PUBLIC_WORKER_URL: string;
}
type TgUser = { id:number; is_bot?:boolean; username?:string; first_name?:string };
type TgChat = { id:number; type:string; title?:string };
type TgMessage = { message_id:number; from?:TgUser; chat:TgChat; text?:string };
type TgCallback = { id:string; from:TgUser; data?:string; message?:TgMessage };
type TgUpdate = { update_id:number; message?:TgMessage; callback_query?:TgCallback };
type BotTokenRow = { tenant_id:number; token_ciphertext:string; token_iv:string; webhook_secret_hash:string };
const SUPER_ADMIN = "am8818888";
const stages = [100,200,400,800,1600,3200];

function json(data:unknown,status=200){return new Response(JSON.stringify(data),{status,headers:{"content-type":"application/json; charset=utf-8","cache-control":"no-store"}});}
function enc(bytes:Uint8Array){return btoa(String.fromCharCode(...bytes));}
function dec(value:string){return Uint8Array.from(atob(value),c=>c.charCodeAt(0));}
async function sha(value:string){return enc(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value))));}
async function encryptToken(env:Env,token:string){
  const key=await crypto.subtle.importKey("raw",dec(env.TOKEN_ENCRYPTION_KEY),{name:"AES-GCM"},false,["encrypt"]);
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const ciphertext=await crypto.subtle.encrypt({name:"AES-GCM",iv},key,new TextEncoder().encode(token));
  return {token_ciphertext:enc(new Uint8Array(ciphertext)),token_iv:enc(iv)};
}
async function decryptToken(env:Env,row:BotTokenRow){
  const key=await crypto.subtle.importKey("raw",dec(env.TOKEN_ENCRYPTION_KEY),{name:"AES-GCM"},false,["decrypt"]);
  const plain=await crypto.subtle.decrypt({name:"AES-GCM",iv:dec(row.token_iv)},key,dec(row.token_ciphertext));
  return new TextDecoder().decode(plain);
}
async function tg<T=any>(token:string,method:string,payload:Record<string,unknown>={}):Promise<T>{
  const r=await fetch(`https://api.telegram.org/bot${token}/${method}`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(payload)});
  const body=await r.json() as {ok:boolean;result:T;description?:string};
  if(!r.ok||!body.ok) throw new Error(body.description||`Telegram API ${r.status}`);
  return body.result;
}
async function reply(token:string,chatId:number,text:string,extra:Record<string,unknown>={}){return tg(token,"sendMessage",{chat_id:chatId,text,...extra});}
async function masterUpdate(env:Env,msg:TgMessage){
  const user=msg.from, text=(msg.text||"").trim();
  if(!user||user.is_bot||msg.chat.type!=="private") return;
  if((user.username||"").toLowerCase()!==SUPER_ADMIN){await reply(env.MASTER_BOT_TOKEN,msg.chat.id,"⛔ 仅总管理员可使用此后台。");return;}
  if(text==="/start"){
    await reply(env.MASTER_BOT_TOKEN,msg.chat.id,"👑 KK 总后台已连接（Cloudflare Webhook）。\n发送：绑定 机器人TOKEN @管理用户名\n发送 /bots 查看已绑定机器人。");
    return;
  }
  if(text==="/bots"){
    const {results}=await env.DB.prepare("SELECT bot_username,owner_username,enabled FROM tenants ORDER BY id").all<{bot_username:string;owner_username:string;enabled:number}>();
    await reply(env.MASTER_BOT_TOKEN,msg.chat.id,results.length?results.map((x,i)=>`${i+1}. @${x.bot_username} → @${x.owner_username} · ${x.enabled?"启用":"停用"}`).join("\n"):"暂无子机器人");
    return;
  }
  const m=text.match(/^绑定\s+(\d{5,}:[A-Za-z0-9_-]{20,})\s+(@?[A-Za-z0-9_]{3,})$/);
  if(!m){await reply(env.MASTER_BOT_TOKEN,msg.chat.id,"指令：绑定 机器人TOKEN @管理用户名\n或发送 /bots");return;}
  const token=m[1],owner=m[2].replace(/^@/,"").toLowerCase();
  if(token===env.MASTER_BOT_TOKEN){await reply(env.MASTER_BOT_TOKEN,msg.chat.id,"不能绑定总后台机器人自身。");return;}
  try{
    const bot=await tg<{id:number;username:string}>(token,"getMe");
    const tenant=await env.DB.prepare("INSERT INTO tenants(bot_id,bot_username,owner_username,owner_user_id) VALUES(?,?,?,NULL) ON CONFLICT(bot_id) DO UPDATE SET bot_username=excluded.bot_username,owner_username=excluded.owner_username RETURNING id").bind(String(bot.id),bot.username,owner).first<{id:number}>();
    if(!tenant) throw new Error("无法创建租户");
    const secrets=enc(crypto.getRandomValues(new Uint8Array(32)));
    const encrypted=await encryptToken(env,token);
    await env.DB.prepare("INSERT INTO bot_credentials(tenant_id,token_ciphertext,token_iv,webhook_secret_hash) VALUES(?,?,?,?) ON CONFLICT(tenant_id) DO UPDATE SET token_ciphertext=excluded.token_ciphertext,token_iv=excluded.token_iv,webhook_secret_hash=excluded.webhook_secret_hash")
      .bind(tenant.id,encrypted.token_ciphertext,encrypted.token_iv,await sha(secrets)).run();
    await env.DB.prepare("INSERT OR IGNORE INTO tenant_settings(tenant_id) VALUES(?)").bind(tenant.id).run();
    await tg(token,"setWebhook",{url:`${env.PUBLIC_WORKER_URL.replace(/\/$/,"")}/webhook/${bot.id}`,secret_token:secrets,allowed_updates:["message","callback_query"]});
    await reply(env.MASTER_BOT_TOKEN,msg.chat.id,`✅ 已登记 @${bot.username}，归属管理员 @${owner}。Webhook 已设置。`);
  }catch(e){await reply(env.MASTER_BOT_TOKEN,msg.chat.id,`绑定失败：${String(e).slice(0,240)}`);}
}
async function childUpdate(env:Env,row:BotTokenRow,update:TgUpdate){
  const token=await decryptToken(env,row), msg=update.message;
  if(msg?.from&&!msg.from.is_bot&&msg.text&&msg.chat.type==="private"){
    const text=msg.text.trim();
    const admin=await env.DB.prepare("SELECT role FROM admins WHERE tenant_id=? AND user_id=?").bind(row.tenant_id,msg.from.id).first<{role:string}>();
    if(text==="/start"){
      if(admin){await reply(token,msg.chat.id,"⚙️ KK 子机器人后台\n/bindgroup：在目标群内绑定群聊\n/status：查看运行设置");}
      else{
        await env.DB.prepare("INSERT INTO player_registrations(tenant_id,player_user_id,username,status) VALUES(?,?,?,'pending') ON CONFLICT(tenant_id,player_user_id) DO NOTHING").bind(row.tenant_id,msg.from.id,msg.from.username||"").run();
        await reply(token,msg.chat.id,"注册申请已提交，请等待管理员验证。");
      }
    }else if(text==="/status"&&admin){
      const s=await env.DB.prepare("SELECT target_chat_title,auto_bet,stage_index FROM tenant_settings WHERE tenant_id=?").bind(row.tenant_id).first<{target_chat_title:string;auto_bet:number;stage_index:number}>();
      await reply(token,msg.chat.id,`目标群：${s?.target_chat_title||"未绑定"}\n自动下注：${s?.auto_bet?"开启":"暂停"}\n倍投关卡：${(s?.stage_index??0)+1}/6（${stages[s?.stage_index??0]}）`);
    }else if(text==="/bindgroup"&&admin){await reply(token,msg.chat.id,"请在目标群内发送 /bindgroup。");}
  }
  if(msg?.from&&!msg.from.is_bot&&msg.text==="/bindgroup"&&msg.chat.type!=="private"){
    const admin=await env.DB.prepare("SELECT 1 FROM admins WHERE tenant_id=? AND user_id=?").bind(row.tenant_id,msg.from.id).first();
    if(admin){
      await env.DB.prepare("INSERT INTO target_groups(tenant_id,chat_id,title) VALUES(?,?,?) ON CONFLICT(tenant_id,chat_id) DO UPDATE SET title=excluded.title,enabled=1").bind(row.tenant_id,msg.chat.id,msg.chat.title||String(msg.chat.id)).run();
      await env.DB.prepare("UPDATE tenant_settings SET target_chat_id=?,target_chat_title=? WHERE tenant_id=?").bind(msg.chat.id,msg.chat.title||String(msg.chat.id),row.tenant_id).run();
      await reply(token,msg.chat.id,"已绑定本群为目标群。");
    }
  }
}
async function webhook(request:Request,env:Env,botId:string){
  const update=await request.json() as TgUpdate;
  if(!Number.isInteger(update.update_id)) return json({ok:false,error:"invalid_update"},400);
  if(botId==="master"){
    if(request.headers.get("x-telegram-bot-api-secret-token")!==env.MASTER_WEBHOOK_SECRET) return json({ok:false},401);
    await env.DB.prepare("INSERT OR IGNORE INTO processed_updates(tenant_id,update_id) VALUES(0,?)").bind(update.update_id).run().catch(()=>{});
    if(update.message) await masterUpdate(env,update.message);
    return json({ok:true});
  }
  const row=await env.DB.prepare("SELECT c.tenant_id,c.token_ciphertext,c.token_iv,c.webhook_secret_hash FROM tenants t JOIN bot_credentials c ON c.tenant_id=t.id WHERE t.bot_id=? AND t.enabled=1").bind(botId).first<BotTokenRow>();
  if(!row) return json({ok:false,error:"unknown_bot"},404);
  const secret=request.headers.get("x-telegram-bot-api-secret-token")||"";
  if(await sha(secret)!==row.webhook_secret_hash) return json({ok:false},401);
  const prior=await env.DB.prepare("INSERT OR IGNORE INTO processed_updates(tenant_id,update_id) VALUES(?,?)").bind(row.tenant_id,update.update_id).run();
  if((prior.meta.changes||0)===0) return json({ok:true,duplicate:true});
  await childUpdate(env,row,update);
  return json({ok:true});
}
export default { async fetch(request:Request,env:Env):Promise<Response>{
  const url=new URL(request.url);
  if(url.pathname==="/"||url.pathname==="/health"){
    if(request.method!=="GET") return json({ok:false},405);
    return json({ok:true,service:"KK Cloudflare Worker",configured:!!(env.MASTER_BOT_TOKEN&&env.MASTER_WEBHOOK_SECRET&&env.TOKEN_ENCRYPTION_KEY&&env.PUBLIC_WORKER_URL)});
  }
  const match=url.pathname.match(/^\/webhook\/(master|\d{5,})$/);
  if(match&&request.method==="POST"){
    try{return await webhook(request,env,match[1]);}catch(e){console.error("webhook_error",e);return json({ok:false,error:"webhook_failed"},500);}
  }
  return json({ok:false,error:"not_found"},404);
}};
