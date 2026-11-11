"""Bale platform adapter — Telegram-compatible API at tapi.bale.ai."""

from __future__ import annotations

import html as _html_std
import logging
import re
from typing import Any, Optional

from agent.i18n import t
from agent.ssl_verify import platform_ssl_context
from gateway.config import Platform, PlatformConfig
from plugins.platforms.telegram.adapter import (
    HTTPXRequest,
    TelegramAdapter,
    _TEXT_SEND_DEADLINE,
    _await_with_thread_deadline,
    check_telegram_requirements,
    telegram_deps_present,
)
from utils import env_float, env_int

from . import gateway_env
from .adapter_hint import PLATFORM_HINT

logger = logging.getLogger(__name__)

BALE_API_BASE = "https://tapi.bale.ai/bot"
BALE_FILE_BASE = "https://tapi.bale.ai/file/bot"

# Telegram MarkdownV2 escapes that Bale renders literally when parse_mode is ignored.
_MDV2_UNESCAPE_RE = re.compile(r"\\([_*\[\]()~`>#+\-=|{}.!\\])")
_HTML_TAG_RE = re.compile(r"</?(?:b|i|pre|code|u|strong|em|a)(?:\s[^>]*)?>", re.I)


def _unescape_mdv2_literals(text: str) -> str:
    """Undo MarkdownV2 backslash escapes (e.g. Self\\-improvement → Self-improvement)."""
    if not text or "\\" not in text:
        return text
    return _MDV2_UNESCAPE_RE.sub(r"\1", text)


def _html_control_to_bale(text: str) -> str:
    """Convert Telegram HTML control prompts (approval/clarify) into Bale auto-Markdown text.

    Bale has no HTML parse_mode; raw ``<b>`` / ``<pre>`` / ``&#x27;`` show literally otherwise.
    """
    if not text:
        return text
    if "<" not in text and "&" not in text:
        return text

    def _bold(m: re.Match) -> str:
        return f" *{_html_std.unescape(m.group(1))}* "

    def _italic(m: re.Match) -> str:
        return f" _{_html_std.unescape(m.group(1))}_ "

    def _pre(m: re.Match) -> str:
        body = _html_std.unescape(m.group(1)).strip("\n")
        return f"\n{body}\n"

    out = re.sub(r"<pre>([\s\S]*?)</pre>", _pre, text, flags=re.I)
    out = re.sub(r"<code>([\s\S]*?)</code>", _pre, out, flags=re.I)
    out = re.sub(r"<b>([\s\S]*?)</b>", _bold, out, flags=re.I)
    out = re.sub(r"<strong>([\s\S]*?)</strong>", _bold, out, flags=re.I)
    out = re.sub(r"<i>([\s\S]*?)</i>", _italic, out, flags=re.I)
    out = re.sub(r"<em>([\s\S]*?)</em>", _italic, out, flags=re.I)
    out = _HTML_TAG_RE.sub("", out)
    return _html_std.unescape(out)


def _to_bale_markdown(content: str) -> str:
    """Convert agent markdown to Bale's auto-Markdown dialect.

    Bale docs: every outbound text is rendered as Markdown (not Telegram MarkdownV2).
    Bold uses spaced single asterisks: `` *bold* ``; italic uses spaced underscores: `` _italic_ ``.
    Links keep GFM ``[text](url)``. No parse_mode is sent — Bale applies Markdown itself.
    """
    if not content:
        return content

    placeholders: dict[str, str] = {}
    counter = [0]

    def _ph(value: str) -> str:
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    text = content

    # Protect fenced code (leave as plain monospace-ish text; Bale has no documented ``` support).
    def _protect_fence(m: re.Match) -> str:
        body = m.group(2) or ""
        return _ph(body.strip("\n") if body.strip() else m.group(0))

    text = re.sub(r"```[^\n]*\n([\s\S]*?)```", _protect_fence, text)
    text = re.sub(r"`([^`]+)`", lambda m: _ph(m.group(1)), text)

    # Protect links so inner text is not re-wrapped.
    def _protect_link(m: re.Match) -> str:
        return _ph(f"[{m.group(1)}]({m.group(2)})")

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _protect_link, text)

    # Headers → Bale bold
    text = re.sub(
        r"^#{1,6}\s+(.+)$",
        lambda m: f" *{m.group(1).strip()}* ",
        text,
        flags=re.MULTILINE,
    )
    # GFM bold/italic → Bale spaced markers
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f" *{m.group(1)}* ", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f" _{m.group(1)}_ ", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", lambda m: f" _{m.group(1)}_ ", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)

    for key, value in placeholders.items():
        text = text.replace(key, value)

    # Never leave MarkdownV2 escapes in the payload.
    return _unescape_mdv2_literals(text)


class BaleAdapter(TelegramAdapter):
    """Reuse Telegram adapter transport against Bale's Bot API base URL."""

    # Bale auto-Markdown is not MarkdownV2; avoid claiming TG code-fence rendering.
    supports_code_blocks = False

    def __init__(self, config: PlatformConfig):
        extra = dict(config.extra or {})
        extra.setdefault("base_url", BALE_API_BASE)
        extra.setdefault("base_file_url", BALE_FILE_BASE)
        # Domestic Bale API — never inherit TELEGRAM_PROXY / telegram.org IP fallback.
        extra["proxy_url"] = None
        config.extra = extra
        super().__init__(config)
        self.platform = Platform("bale")
        # Telegram Bot API 10.1 rich path is not available on Bale.
        self._rich_messages_enabled = False
        self._rich_drafts_enabled = False
        self._rich_send_disabled = True
        self._rich_draft_disabled = True

    def format_message(self, content: str) -> str:
        """Bale Markdown (auto) — never Telegram MarkdownV2 escaping."""
        return _to_bale_markdown(content)

    def _should_attempt_rich(self, content: str, metadata: Optional[dict[str, Any]] = None) -> bool:
        return False

    # --- Exec-approval / clarify cards: Telegram uses HTML; Bale must not. ---
    @property
    def _EA_HEADER(self) -> str:
        return f"⚠️  *{t('gateway.exec_approval.header')}* \n\n"

    _EA_CODE_OPEN = ""
    _EA_CODE_CLOSE = "\n\n"

    @property
    def _EA_REASON_LABEL(self) -> str:
        return f"{t('gateway.exec_approval.reason_label')}: "

    @property
    def _EA_SMART_DENY_LINE(self) -> str:
        return f"\n\n *{t('gateway.exec_approval.smart_deny_line')}* "

    def _ea_escape(self, text: str) -> str:
        # Do not html.escape — that produces &#x27; / &lt; which Bale shows raw.
        return text or ""

    async def _send_control_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: Any,
        thread_id: Optional[str],
        metadata: Optional[dict[str, Any]],
        reply_markup: Any = None,
        reply_to_mode: Optional[str] = None,
    ):
        """Control prompts without HTML/MarkdownV2 parse_mode (Bale auto-Markdown only)."""
        from plugins.platforms.telegram.adapter import normalize_telegram_chat_id

        text = _html_control_to_bale(text)
        text = _unescape_mdv2_literals(text)
        reply_to_id = self._reply_to_message_id_for_send(None, metadata, reply_to_mode=reply_to_mode)
        kwargs: dict[str, Any] = {
            "chat_id": normalize_telegram_chat_id(chat_id),
            "text": text,
            "parse_mode": None,
            **self._link_preview_kwargs(),
        }
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        kwargs["reply_to_message_id"] = reply_to_id
        kwargs.update(
            self._thread_kwargs_for_send(
                chat_id,
                thread_id,
                metadata,
                reply_to_message_id=reply_to_id,
                reply_to_mode=reply_to_mode,
            )
        )
        return await self._send_message_with_thread_fallback(**kwargs)

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[dict[str, Any]] = None,
    ):
        """Clarify card without HTML tags (Bale has no HTML parse_mode)."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        def build():
            text = f"❓ {question}"
            keyboard = None
            if choices:
                text += "\n\n" + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(choices))
                rows = [
                    [InlineKeyboardButton(str(idx + 1), callback_data=f"cl:{clarify_id}:{idx}")]
                    for idx in range(len(choices))
                ]
                rows.append(
                    [InlineKeyboardButton(t("platform.telegram.prompt.other"), callback_data=f"cl:{clarify_id}:other")]
                )
                keyboard = InlineKeyboardMarkup(rows)
            return text, keyboard, lambda msg: self._clarify_state.__setitem__(clarify_id, session_key)

        return await self._send_prompt(
            "send_clarify",
            chat_id,
            metadata,
            build,
            parse_mode=None,
            thread_id=self._metadata_thread_id(metadata),
        )

    def _fallback_ips(self):
        return []

    async def _send_chunk_markdown_or_plain(self, chunk: str, send_kwargs: dict[str, Any]):
        """Send without parse_mode; Bale always applies its own Markdown renderer."""
        text = _unescape_mdv2_literals(chunk)
        # Parent may have MDV2-escaped chunk indicators like " \\(1/2\\)".
        text = re.sub(r" \\\((\d+)/(\d+)\\\)\s*$", r" (\1/\2)", text)
        return await _await_with_thread_deadline(
            self._bot.send_message(text=text, parse_mode=None, **send_kwargs),
            timeout=_TEXT_SEND_DEADLINE,
            label="bale-send",
            dump_on_blocked_loop=False,
        )

    async def _edit_markdown_or_plain(
        self, chat_id: str, message_id: str, formatted: str, plain: str, warn_fmt: str
    ) -> bool:
        text = _unescape_mdv2_literals(formatted) if formatted else (plain or "")
        try:
            await self._edit_text(chat_id, message_id, text, None)
        except Exception as exc:
            if "not modified" in str(exc).lower():
                return True
            logger.warning(warn_fmt, self.name, exc)
            if plain and plain != text:
                await self._edit_text(chat_id, message_id, _unescape_mdv2_literals(plain), None)
        return False

    async def _build_ptb_requests(self) -> tuple:
        """Direct HTTPS to tapi.bale.ai — no Telegram proxy and no api.telegram.org fallback."""
        request_kwargs = {
            "connection_pool_size": env_int("HERMES_TELEGRAM_HTTP_POOL_SIZE", 512),
            "pool_timeout": env_float("HERMES_TELEGRAM_HTTP_POOL_TIMEOUT", 8.0),
            "connect_timeout": env_float("HERMES_TELEGRAM_HTTP_CONNECT_TIMEOUT", 10.0),
            "read_timeout": env_float("HERMES_TELEGRAM_HTTP_READ_TIMEOUT", 20.0),
            "write_timeout": env_float("HERMES_TELEGRAM_HTTP_WRITE_TIMEOUT", 20.0),
            "media_write_timeout": 60.0,
        }
        from gateway.platforms._http_client_limits import platform_httpx_limits

        _base_limits = platform_httpx_limits()
        if _base_limits is not None:
            import httpx as _httpx

            _pool_limits = _httpx.Limits(
                max_connections=request_kwargs["connection_pool_size"],
                max_keepalive_connections=_base_limits.max_keepalive_connections,
                keepalive_expiry=_base_limits.keepalive_expiry,
            )
            _updates_limits = _httpx.Limits(
                max_connections=request_kwargs["connection_pool_size"],
                max_keepalive_connections=0,
                keepalive_expiry=_base_limits.keepalive_expiry,
            )
        else:  # pragma: no cover
            _pool_limits = _updates_limits = None

        def _with_limits(httpx_kwargs: Optional[dict] = None) -> dict:
            kwargs = dict(httpx_kwargs or {})
            if _pool_limits is not None and "limits" not in kwargs:
                kwargs["limits"] = _pool_limits
            return kwargs

        def _pair(general_httpx: dict, updates_httpx: dict, **extra) -> tuple:
            return (
                HTTPXRequest(
                    **request_kwargs,
                    **extra,
                    httpx_kwargs={"verify": platform_ssl_context(), **general_httpx},
                ),
                HTTPXRequest(
                    **request_kwargs,
                    **extra,
                    httpx_kwargs={"verify": platform_ssl_context(), **updates_httpx},
                ),
            )

        logger.info("[%s] Bale direct transport to %s", self.name, self.config.extra.get("base_url"))
        request, get_updates_request = _pair(_with_limits(), {"limits": _updates_limits})
        return request, self._instrument_polling_request(get_updates_request)


def _build_adapter(config):
    adapter = BaleAdapter(config)
    try:
        adapter._notifications_mode = "important"
    except Exception:
        pass
    return adapter


def _is_connected(config) -> bool:
    token = getattr(config, "token", None)
    if not token:
        import hermes_cli.gateway as gateway_mod

        token = gateway_mod.get_env_value("BALE_BOT_TOKEN") or ""
    return bool(str(token).strip())


async def _standalone_send(
    pconfig,
    chat_id,
    message,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
):
    token = getattr(pconfig, "token", None)
    if not token:
        from agent.secret_scope import get_secret

        token = get_secret("BALE_BOT_TOKEN", "") or ""
    extra = getattr(pconfig, "extra", {}) or {}
    base_url = str(extra.get("base_url") or BALE_API_BASE)
    text = _to_bale_markdown(message)
    disable_link_previews = bool(extra.get("disable_link_previews"))
    from tools.send_message_tool import _send_telegram

    try:
        return await _send_telegram(
            token,
            chat_id,
            text,
            media_files=media_files,
            thread_id=thread_id,
            disable_link_previews=disable_link_previews,
            force_document=force_document,
            base_url=base_url,
            parse_mode=None,
        )
    except TypeError:
        return await _send_telegram(
            token,
            chat_id,
            text,
            media_files=media_files,
            thread_id=thread_id,
            disable_link_previews=disable_link_previews,
            force_document=force_document,
            base_url=base_url,
        )


def _apply_yaml_config(yaml_cfg: dict, bale_cfg: dict) -> dict | None:
    extras: dict[str, Any] = {
        "base_url": BALE_API_BASE,
        "base_file_url": BALE_FILE_BASE,
    }
    nested = bale_cfg.get("extra") if isinstance(bale_cfg.get("extra"), dict) else {}
    for key in ("base_url", "base_file_url", "disable_link_previews", "reply_to_mode"):
        if key in bale_cfg:
            extras[key] = bale_cfg[key]
        elif key in nested:
            extras[key] = nested[key]
    for key, value in nested.items():
        extras.setdefault(key, value)
    return extras


def register(ctx) -> None:
    ctx.register_platform(
        name="bale",
        label="Bale",
        adapter_factory=_build_adapter,
        check_fn=telegram_deps_present,
        ensure_deps_fn=check_telegram_requirements,
        is_connected=_is_connected,
        required_env=["BALE_BOT_TOKEN"],
        apply_yaml_config_fn=_apply_yaml_config,
        cron_deliver_env_var="BALE_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=4096,
        emoji="💬",
        platform_hint=PLATFORM_HINT,
        display_tier="high",
        display_overrides={"tool_progress": "off", "busy_ack_detail": False},
        env_overrides=gateway_env.ENV_OVERRIDES,
        env_enable_credentials=gateway_env.ENV_ENABLE_CREDENTIALS,
        **gateway_env.AUTHZ,
    )
