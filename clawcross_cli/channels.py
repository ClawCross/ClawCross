"""
Channel setup catalog for the ClawCross CLI.

Mirrors the channels exposed by the mobile creator tab
(frontend/templates/group_chat_mobile.html:9657 MOBILE_CHATBOT_CHANNEL_FALLBACKS)
so anything the user can configure in the UI can also be set up from
the terminal. The CLI never talks to the backend's settings service —
it just writes the same env vars that ``src/api/settings_service.py``
already reads from ``~/.clawcross/config/.env``.

Two storage shapes:

  kind = "bots_json"   (default; all NoneBot adapters)
      Writes a JSON array under ``env_key`` (e.g.
      ``TELEGRAM_BOTS=[{"token":"...","name":"bot1"}]``).
      Each ``BotField.name`` is a JSON key inside one bot entry.

  kind = "env_vars"    (weclaw, webhook)
      Writes each ``BotField.name`` as its own env var (no JSON wrap).
      Used by channels whose backend config lives in multiple env vars
      (e.g. WECLAW_USERNAME / WECLAW_BIN / WECLAW_PROXY_HOST).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils.chatbot_channel_catalog import get_chatbot_channels


@dataclass
class BotField:
    name: str                       # JSON key (bots_json) or env var name (env_vars)
    prompt: str
    password: bool = False
    required: bool = False
    default: str = ""
    help: str = ""
    target: str = "bot"             # "bot" | "bot_intents" | "env"
    env_key: str = ""
    path: str = ""
    field_type: str = "text"
    pattern: str = ""
    invalid_message: str = ""


@dataclass
class ChannelInfo:
    id: str
    label: str
    env_key: str                    # primary env var name; empty for env_vars-only channels
    kind: str = "bots_json"         # "bots_json" | "env_vars"
    adapter: str = ""
    module: str = ""
    package: str = ""
    config_field: str = ""
    env_aliases: list[str] = field(default_factory=list)
    requires_config: bool = True
    required_env: list[str] = field(default_factory=list)
    emoji: str = ""
    setup_instructions: list[str] = field(default_factory=list)
    bot_fields: list[BotField] = field(default_factory=list)
    notes: str = ""

    def has_setup(self) -> bool:
        return bool(self.bot_fields)


# Common NoneBot prelude — shown above any nonebot adapter setup.
_NONEBOT_PRELUDE = (
    "Each entry below becomes one item in the JSON array stored in "
    "the env var. Repeat `channel setup <id>` to add more bots."
)


CHANNELS: dict[str, ChannelInfo] = {
    # ── NoneBot adapters (bots_json) ─────────────────────────────────────────
    "telegram": ChannelInfo(
        id="telegram", label="Telegram", env_key="TELEGRAM_BOTS", emoji="📱",
        setup_instructions=[
            "1. Open Telegram and message @BotFather",
            "2. Send /newbot, name the bot, copy the token",
            "3. (Optional) message @userinfobot to get your numeric user ID",
        ],
        bot_fields=[
            BotField("token", "Bot token", password=True,
                     help="The token from @BotFather."),
            BotField("name", "Local bot label", default="bot1"),
        ],
    ),
    "qq": ChannelInfo(
        id="qq", label="QQ (Official)", env_key="QQ_BOTS", emoji="🐧",
        setup_instructions=[
            "1. https://q.qq.com → register a QQ bot",
            "2. Copy AppID, AppSecret and Token",
            "3. (Optional) set QQ_IS_SANDBOX=1 in .env for the sandbox channel",
        ],
        bot_fields=[
            BotField("id", "AppID"),
            BotField("secret", "AppSecret", password=True),
            BotField("token", "Token", password=True, default=""),
        ],
        notes="Intents JSON can be set manually in .env (see mobile UI hint).",
    ),
    "onebotv11": ChannelInfo(
        id="onebotv11", label="OneBot V11", env_key="ONEBOTV11_BOTS", emoji="🤖",
        setup_instructions=[
            "1. Stand up a OneBot V11 implementation (go-cqhttp, Lagrange, NapCat, etc.)",
            "2. Point it at NoneBot's reverse WS endpoint (default ws://127.0.0.1:8120/onebot/v11/ws)",
            "3. Configure an access_token in BOTH the impl and here so they match",
        ],
        bot_fields=[
            BotField("access_token", "Shared access token", password=True),
            BotField("name", "Local bot label", default="bot1"),
        ],
    ),
    "onebotv12": ChannelInfo(
        id="onebotv12", label="OneBot V12", env_key="ONEBOTV12_BOTS", emoji="🤖",
        setup_instructions=[
            "1. Stand up a OneBot V12 implementation",
            "2. Configure reverse-WS pointing at NoneBot endpoint",
        ],
        bot_fields=[
            BotField("access_token", "Shared access token", password=True),
            BotField("impl", "Impl name (e.g. walleq)", default=""),
            BotField("platform", "Platform tag (e.g. qq)", default=""),
        ],
    ),
    "discord": ChannelInfo(
        id="discord", label="Discord", env_key="DISCORD_BOTS", emoji="💬",
        setup_instructions=[
            "1. https://discord.com/developers/applications → New Application",
            "2. Bot → Reset Token → copy it",
            "3. Bot → Privileged Gateway Intents → enable Message Content Intent",
            "4. OAuth2 → URL Generator: scopes bot + applications.commands;",
            "   permissions Send Messages, Read History, Attach Files",
        ],
        bot_fields=[
            BotField("token", "Bot token", password=True),
            BotField("name", "Local bot label", default="bot1"),
        ],
    ),
    "dingtalk": ChannelInfo(
        id="dingtalk", label="DingTalk", env_key="DINGTALK_BOTS", emoji="🔔",
        setup_instructions=[
            "1. https://open-dev.dingtalk.com → create app",
            "2. Application Info → copy AppKey + AppSecret",
            "3. Capabilities → Bot → enable",
        ],
        bot_fields=[
            BotField("app_key", "AppKey"),
            BotField("app_secret", "AppSecret", password=True),
        ],
    ),
    "feishu": ChannelInfo(
        id="feishu", label="Feishu / Lark", env_key="FEISHU_BOTS", emoji="🪶",
        setup_instructions=[
            "1. https://open.feishu.cn/app → Create Custom App",
            "2. Credentials & Basic Info → copy AppID + AppSecret",
            "3. Bot → enable, set callback URL pointing at NoneBot",
            "4. Permissions: im:message, im:message.send_as_bot",
        ],
        bot_fields=[
            BotField("app_id", "App ID"),
            BotField("app_secret", "App Secret", password=True),
            BotField("encrypt_key", "Encrypt key (or empty)", password=True, default=""),
            BotField("verification_token", "Verification token (or empty)", password=True, default=""),
        ],
    ),
    "kaiheila": ChannelInfo(
        id="kaiheila", label="Kaiheila / KOOK", env_key="KAIHEILA_BOTS", emoji="🪁",
        setup_instructions=[
            "1. https://developer.kookapp.cn/ → create application",
            "2. Bot → Connect Type → Webhook or WebSocket",
            "3. Copy the Bot Token",
        ],
        bot_fields=[
            BotField("token", "Bot token", password=True),
            BotField("name", "Local bot label", default="bot1"),
        ],
    ),
    "mail": ChannelInfo(
        id="mail", label="Mail (IMAP/SMTP)", env_key="MAIL_BOTS", emoji="📧",
        setup_instructions=[
            "1. Pick an IMAP+SMTP capable account (Gmail, Outlook, custom server)",
            "2. For Gmail-style 2FA: generate an app password",
            "3. Allow IMAP / SMTP on the mailbox if disabled by default",
        ],
        bot_fields=[
            BotField("username", "Email address"),
            BotField("password", "App password / mailbox password", password=True),
            BotField("imap_host", "IMAP host", default="imap.gmail.com"),
            BotField("imap_port", "IMAP port", default="993"),
            BotField("smtp_host", "SMTP host", default="smtp.gmail.com"),
            BotField("smtp_port", "SMTP port", default="587"),
        ],
    ),
    "minecraft": ChannelInfo(
        id="minecraft", label="Minecraft", env_key="MINECRAFT_BOTS", emoji="⛏",
        setup_instructions=[
            "1. Enable RCON on your Minecraft server (server.properties: enable-rcon=true)",
            "2. Set an rcon.password and rcon.port",
            "3. Make sure the port is reachable from the ClawCross host",
        ],
        bot_fields=[
            BotField("host", "Server host"),
            BotField("port", "RCON port", default="25575"),
            BotField("password", "RCON password", password=True),
        ],
    ),
    "github": ChannelInfo(
        id="github", label="GitHub App / Webhook", env_key="GITHUB_BOTS", emoji="🐙",
        setup_instructions=[
            "1. GitHub → Settings → Developer Settings → GitHub Apps → New",
            "2. Configure webhook URL pointing at NoneBot (/github/webhooks)",
            "3. Note the App ID, generate a private key, and set a webhook secret",
        ],
        bot_fields=[
            BotField("app_id", "App ID"),
            BotField("private_key_path", "Path to the .pem private key", default=""),
            BotField("webhook_secret", "Webhook secret", password=True, default=""),
        ],
    ),
    "villa": ChannelInfo(
        id="villa", label="Villa (MiHoYo)", env_key="VILLA_BOTS", emoji="🏰",
        setup_instructions=[
            "1. https://dev.mihoyo.com/villa → create villa bot",
            "2. Copy Bot ID and Bot Secret",
            "3. Configure callback / webhook to NoneBot",
        ],
        bot_fields=[
            BotField("bot_id", "Bot ID"),
            BotField("bot_secret", "Bot Secret", password=True),
            BotField("verify_token", "Pub-key Verify token (or empty)",
                     password=True, default=""),
        ],
    ),
    "yunhu": ChannelInfo(
        id="yunhu", label="Yunhu / 云湖", env_key="YUNHU_BOTS", emoji="☁",
        setup_instructions=[
            "1. https://www.yhchat.com/developer → register a Yunhu bot",
            "2. Copy the Bot Token",
        ],
        bot_fields=[
            BotField("token", "Bot token", password=True),
            BotField("name", "Local bot label", default="bot1"),
        ],
    ),
    "heybox": ChannelInfo(
        id="heybox", label="Heybox / 小黑盒", env_key="HEYBOX_BOTS", emoji="📦",
        setup_instructions=[
            "1. https://chat.xiaoheihe.cn/developer → create a bot",
            "2. Copy the Bot Token / open API key",
        ],
        bot_fields=[
            BotField("token", "Bot token", password=True),
        ],
    ),
    "console": ChannelInfo(
        id="console", label="Console (local testing)", env_key="CONSOLE_BOTS", emoji="🖥",
        setup_instructions=[
            "1. No external setup needed — Console adapter ships with NoneBot.",
            "2. Useful for testing prompts without hitting a real chat platform.",
        ],
        bot_fields=[
            BotField("name", "Console label", default="console"),
        ],
    ),

    # ── WeClaw (env_vars; mirrors mobile MOBILE_CHATBOT_WECLAW_KEYS) ─────────
    "weclaw": ChannelInfo(
        id="weclaw", label="微信 / WeClaw", env_key="", kind="env_vars", emoji="🟢",
        setup_instructions=[
            "1. Install / link the weclaw binary (often `pip install weclaw` or",
            "   download from GitHub releases; CLI auto-resolves WECLAW_BIN)",
            "2. `clawcross channel setup weclaw`     — fill in the proxy settings",
            "3. `clawcross channel login weclaw`     — scan the ASCII QR in your terminal",
            "4. `clawcross channel status weclaw`    — verify session is alive",
            "   (`clawcross channel logout weclaw`   stops the daemon when done)",
        ],
        bot_fields=[
            BotField("WECLAW_ENABLED", "Enable WeClaw (true/false)", default="true"),
            BotField("WECLAW_USERNAME", "Local WeClaw account name", default="default"),
            BotField("WECLAW_BIN", "Path to the weclaw binary", default="weclaw"),
            BotField("WECLAW_CONFIG", "Path to weclaw config.json",
                     default="~/.weclaw/config.json"),
            BotField("WECLAW_PROXY_HOST", "Proxy host (loopback)", default="127.0.0.1"),
            BotField("WECLAW_PROXY_PORT", "Proxy port", default="51298"),
            BotField("WECLAW_AUTO_INSTALL", "Auto-install on first run (true/false)",
                     default="true"),
        ],
        notes="WeClaw stores credentials inside its own config file; CLI only sets env vars.",
    ),

    # ── Custom webhook (env_vars; needs a shared whitelist file) ────────────
    "webhook": ChannelInfo(
        id="webhook", label="Custom Webhook", env_key="", kind="env_vars", emoji="🔗",
        setup_instructions=[
            "1. Decide the inbound URL that callers POST messages to",
            "2. Generate or copy a shared-secret token",
            "3. Optionally point WHITELIST_FILE at a shared allow-list JSON",
        ],
        bot_fields=[
            BotField("WEBHOOK_SECRET", "Shared-secret token", password=True),
            BotField("WHITELIST_FILE", "Allow-list JSON path (or empty)", default=""),
        ],
    ),
}


def _channels_from_shared_catalog() -> dict[str, ChannelInfo]:
    loaded: dict[str, ChannelInfo] = {}
    for raw in get_chatbot_channels():
        channel_id = str(raw.get("id") or "").strip()
        if not channel_id:
            continue
        raw_kind = str(raw.get("kind") or "nonebot").strip()
        kind = "bots_json" if raw_kind == "nonebot" else "env_vars"
        fields = []
        for item in raw.get("fields") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            field_type = str(item.get("type") or "text").lower()
            target = str(item.get("target") or ("bot" if kind == "bots_json" else "env"))
            fields.append(BotField(
                name=str(item.get("name") or ""),
                prompt=str(item.get("label") or item.get("name") or ""),
                password=field_type == "password",
                required=bool(item.get("required", False)),
                default=str(item.get("default") or ""),
                help=str(item.get("help") or ""),
                target=target,
                env_key=str(item.get("env_key") or item.get("name") or ""),
                path=str(item.get("path") or ""),
                field_type=field_type,
                pattern=str(item.get("pattern") or ""),
                invalid_message=str(item.get("invalid_message") or ""),
            ))
        loaded[channel_id] = ChannelInfo(
            id=channel_id,
            label=str(raw.get("label") or channel_id),
            env_key=str(raw.get("env_key") or ""),
            kind=kind,
            adapter=str(raw.get("adapter") or channel_id),
            module=str(raw.get("module") or ""),
            package=str(raw.get("package") or ""),
            config_field=str(raw.get("config_field") or ""),
            env_aliases=[str(x) for x in raw.get("env_aliases") or []],
            requires_config=bool(raw.get("requires_config", True)),
            required_env=[str(x) for x in raw.get("required_env") or []],
            emoji=str(raw.get("emoji") or ""),
            setup_instructions=[str(x) for x in raw.get("setup_instructions") or []],
            bot_fields=fields,
            notes=str(raw.get("help") or raw.get("notes") or ""),
        )
    return loaded


_shared_channels = _channels_from_shared_catalog()
if _shared_channels:
    CHANNELS = _shared_channels


def list_channels() -> list[ChannelInfo]:
    return list(CHANNELS.values())


def get_channel(channel_id: str) -> ChannelInfo | None:
    key = (channel_id or "").strip().lower()
    if not key:
        return None
    direct = CHANNELS.get(key)
    if direct is not None:
        return direct
    for channel in CHANNELS.values():
        if key == (channel.adapter or "").strip().lower():
            return channel
    return None
