"""card_sender.py — deliver a rendered card PNG to the operator's Telegram via the app's "buzz" bot.

Obtains the bot token + chat_id READ-ONLY via trading_corp.utils.secrets.load_secrets() (a leaf utility
that loads env + Azure Key Vault; it does NOT import or start the trading engine / observer / trade path).
Sends via the Telegram Bot API sendPhoto (raw HTTP through `requests`, so we don't depend on
python-telegram-bot internals). On failure, falls back to sendMessage with a short note.

NEVER prints/logs the token value. NEVER raises into the caller — always returns a bool.
"""
import logging

log = logging.getLogger("card_sender")

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 30


def _load_secrets():
    """Import + call load_secrets defensively. Returns a Secrets-like object or None."""
    try:
        from trading_corp.utils.secrets import load_secrets  # leaf utility, read-only
    except Exception as e:  # ImportError or anything during import
        log.error("card_sender: could not import load_secrets: %s", e)
        return None
    try:
        return load_secrets()
    except Exception as e:
        log.error("card_sender: load_secrets() raised: %s", e)
        return None


def check() -> bool:
    """Return has_telegram (bool) — used by card_watcher --check. Never raises."""
    secrets = _load_secrets()
    if secrets is None:
        return False
    try:
        return bool(secrets.has_telegram)
    except Exception as e:
        log.error("card_sender: has_telegram access raised: %s", e)
        return False


def send_card(png_path, caption) -> bool:
    """Send the card PNG with a caption to the buzz chat. Returns True on success, False otherwise.

    Never raises. On sendPhoto failure, tries sendMessage with a note so a delivery failure still
    surfaces *something* to the operator (link/text fallback acceptable).
    """
    try:
        import requests
    except Exception as e:
        log.error("card_sender: requests not importable: %s", e)
        return False

    secrets = _load_secrets()
    if secrets is None:
        log.error("card_sender: no secrets available; cannot send")
        return False
    try:
        if not secrets.has_telegram:
            log.warning("card_sender: has_telegram=False (token/chat_id missing); skipping send")
            return False
        token = secrets.telegram_bot_token
        chat_id = secrets.telegram_chat_id
    except Exception as e:
        log.error("card_sender: reading telegram secrets raised: %s", e)
        return False

    if not token or not chat_id:
        log.warning("card_sender: token or chat_id empty; skipping send")
        return False

    # --- primary: sendPhoto (attach the PNG) ---
    try:
        with open(png_path, "rb") as fh:
            resp = requests.post(
                _API.format(token=token, method="sendPhoto"),
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": fh},
                timeout=_TIMEOUT,
            )
        if resp.ok and _telegram_ok(resp):
            log.info("card_sender: sendPhoto OK for %s", png_path)
            return True
        log.error("card_sender: sendPhoto failed (status=%s, ok=%s)", resp.status_code, _telegram_ok(resp))
    except Exception as e:
        log.error("card_sender: sendPhoto raised: %s", e)

    # --- fallback: sendMessage with a note ---
    try:
        note = f"{caption}\n\n(card image could not be attached — see engine log)"
        resp = requests.post(
            _API.format(token=token, method="sendMessage"),
            data={"chat_id": chat_id, "text": note},
            timeout=_TIMEOUT,
        )
        if resp.ok and _telegram_ok(resp):
            log.info("card_sender: sendMessage fallback OK")
            return True
        log.error("card_sender: sendMessage fallback failed (status=%s)", resp.status_code)
    except Exception as e:
        log.error("card_sender: sendMessage fallback raised: %s", e)

    return False


def _telegram_ok(resp) -> bool:
    """Telegram returns {'ok': true, ...} on success. Treat unparseable body as not-ok."""
    try:
        return bool(resp.json().get("ok"))
    except Exception:
        return False
