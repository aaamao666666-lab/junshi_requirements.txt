PRAGMA foreign_keys = ON;

CREATE TABLE tenants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL UNIQUE,
  bot_username TEXT NOT NULL DEFAULT '',
  owner_username TEXT NOT NULL DEFAULT '',
  owner_user_id INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Bot tokens are encrypted by the Worker with an AES-GCM key stored as a
-- Cloudflare secret. Never put raw Telegram tokens in D1 or source control.
CREATE TABLE bot_credentials (
  tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  token_ciphertext TEXT NOT NULL,
  token_iv TEXT NOT NULL,
  webhook_secret_hash TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE admins (
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'owner' CHECK (role IN ('owner','admin')),
  PRIMARY KEY (tenant_id,user_id)
);

CREATE TABLE target_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  chat_id INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (tenant_id,chat_id)
);

CREATE TABLE player_registrations (
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  player_user_id INTEGER NOT NULL,
  username TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  approved_by INTEGER,
  approved_at INTEGER,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (tenant_id,player_user_id)
);

CREATE TABLE player_group_bindings (
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  player_user_id INTEGER NOT NULL,
  group_id INTEGER NOT NULL REFERENCES target_groups(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (tenant_id,player_user_id,group_id)
);

CREATE TRIGGER enforce_player_two_groups
BEFORE INSERT ON player_group_bindings
WHEN (SELECT COUNT(*) FROM player_group_bindings
      WHERE tenant_id=NEW.tenant_id AND player_user_id=NEW.player_user_id) >= 2
BEGIN
  SELECT RAISE(ABORT,'player_group_limit_exceeded');
END;

CREATE TABLE bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  player_user_id INTEGER NOT NULL,
  group_id INTEGER REFERENCES target_groups(id) ON DELETE SET NULL,
  game_code TEXT NOT NULL,
  issue TEXT NOT NULL,
  selection TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount > 0),
  odds REAL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','won','lost','cancelled')),
  profit REAL NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (tenant_id,game_code,issue,player_user_id,selection)
);
CREATE INDEX idx_bets_tenant_issue ON bets(tenant_id,game_code,issue);
CREATE INDEX idx_bets_player ON bets(tenant_id,player_user_id,created_at);

CREATE TABLE tenant_daily_stats (
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  bet_count INTEGER NOT NULL DEFAULT 0,
  win_count INTEGER NOT NULL DEFAULT 0,
  turnover REAL NOT NULL DEFAULT 0,
  profit REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id,day)
);

CREATE TABLE tenant_settings (
  tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  target_chat_id INTEGER,
  target_chat_title TEXT NOT NULL DEFAULT '未绑定群聊',
  auto_bet INTEGER NOT NULL DEFAULT 0 CHECK (auto_bet IN (0,1)),
  stage_index INTEGER NOT NULL DEFAULT 0 CHECK (stage_index BETWEEN 0 AND 5),
  enable_group_posts INTEGER NOT NULL DEFAULT 0 CHECK (enable_group_posts IN (0,1))
);

CREATE TABLE processed_updates (
  tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  update_id INTEGER NOT NULL,
  processed_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (tenant_id,update_id)
);
