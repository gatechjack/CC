"""Env-var-based secrets loader. Refuses to start LIVE mode without required keys."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Keys that, if present in a string, must be redacted from logs.
# Catches "KEY_NAME=value" forms (env echoes, .env-loading messages, etc.).
# For third-party libraries that log raw secret VALUES without the key
# prefix (e.g. py-clob-client logging the Polygon private key during
# signing), also see `register_redact_literal` below — that mechanism
# handles value-substring redaction for known-loaded secrets.
_SECRET_KEY_NAMES = (
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "ROBINHOOD_PASSWORD",
    "ROBINHOOD_MFA_SECRET",
    "COINBASE_API_KEY",
    "COINBASE_API_SECRET",
    "COINBASE_PASSPHRASE",
    "COINBASE_FUTURES_API_KEY",
    "COINBASE_FUTURES_API_SECRET",
    "COINBASE_FUTURES_PASSPHRASE",
    "BITUNIX_FUTURES_API_KEY",
    "BITUNIX_FUTURES_API_SECRET",
    "FIDELITY_PASSWORD",
    # Polymarket / Polygon — Phase 0 of the Polymarket Arbitrage division.
    # Private key is the most sensitive (signs USDC-spending transactions);
    # funder address is public-info-by-design but kept on the redact list
    # for defense-in-depth (link analysis from logs is something to deny);
    # RPC URL contains an embedded Alchemy API key in its path segment.
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_FUNDER_ADDRESS",
    "POLYMARKET_COPY_PRIVATE_KEY",
    "POLYMARKET_COPY_FUNDER_ADDRESS",
    "POLYGON_RPC_URL",
    # Kalshi (Phase K1 — read-only). API key ID is a UUID (low sensitivity
    # alone) but keep on the redact list for defense-in-depth. The RSA
    # private key PEM is the load-bearing secret — signs every Kalshi
    # request.
    "KALSHI_API_KEY_ID",
    "KALSHI_PRIVATE_KEY_PEM",
    # Apify (Phase K3 — Kalshi Copy Trading). Token authenticates calls to
    # the saswave Kalshi leaderboard + profile actors used for whale
    # discovery and position monitoring. Free/Starter tier; pulled from KV.
    "APIFY_API_TOKEN",
    # Tastytrade (data provider live since 2026-05-22; Tasty Options
    # division adds order placement on top). OAuth-refresh-token model:
    # provider_secret is the long-lived OAuth client secret. refresh_token
    # is ALSO long-lived — the SDK's `Session.refresh()` exchanges it for
    # a short-lived access (session) token but does NOT self-rotate the
    # refresh_token itself (verified 2026-05-29 against tastytrade SDK
    # source; see `[[tastytrade-refresh-token-no-self-rotation]]`).
    # Both rotate manually per `runbooks/tastytrade_oauth_rotation.md`.
    "TASTYTRADE_PROVIDER_SECRET",
    "TASTYTRADE_REFRESH_TOKEN",
)


# Module-level set of literal secret VALUES (not key names) that must be
# redacted wherever they appear in log output, regardless of context.
# Populated by `register_redact_literal()` after `load_secrets()` resolves
# each secret's actual value. This is the defense against third-party
# libraries (py-clob-client, web3.py, etc.) that may log raw key values
# during DEBUG without the `KEY_NAME=` prefix that `_REDACT_PATTERN`
# relies on.
_REDACT_LITERALS: set[str] = set()


def register_redact_literal(value: str | None) -> None:
    """Register a literal secret value to be redacted from log output.

    Called from `load_secrets()` for each sensitive value once it's
    resolved (from .env or KV). The RedactingFilter then substitutes the
    exact value with ***REDACTED*** anywhere it appears in any log line,
    including third-party library output.

    No-op for empty / None / very-short strings (would risk false-positive
    redactions of non-secret common substrings).
    """
    if not value or len(value) < 16:
        return
    _REDACT_LITERALS.add(value)


@dataclass(frozen=True)
class PolymarketWallet:
    """One Polymarket EOA: signer key + its public funder address.

    Per-division (item 6, 2026-05-29). signature_type=EOA, funder == signer.
    A wallet with either field unset → the broker stubs for that division.
    """
    private_key: str | None
    funder_address: str | None


# Division-slug → (private-key env var, funder env var). Explicit (not
# slug-derived) so it's greppable, a new division is one line, and a typo
# fails to a logged stub rather than silently. arb keeps its LEGACY env
# names (no KV churn — migration option (i)); PCT gets POLYMARKET_COPY_*.
# If arb's wallet is ever migrated, rename to POLYMARKET_ARB_* at that
# moment for uniformity — not before. RPC URL is SHARED (not per-division).
_POLYMARKET_WALLET_ENV: dict[str, tuple[str, str]] = {
    "polymarket_arbitrage": ("POLYMARKET_PRIVATE_KEY", "POLYMARKET_FUNDER_ADDRESS"),
    "polymarket_copy_trading": ("POLYMARKET_COPY_PRIVATE_KEY", "POLYMARKET_COPY_FUNDER_ADDRESS"),
}


@dataclass(frozen=True)
class Secrets:
    anthropic_api_key: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    robinhood_username: str | None
    robinhood_password: str | None
    robinhood_mfa_secret: str | None
    coinbase_api_key: str | None
    coinbase_api_secret: str | None
    coinbase_passphrase: str | None
    # Separate credentials for the FCM futures endpoint. Spot keys are
    # rejected by futures endpoints (and vice versa) — different portfolios
    # in Coinbase, different signing scopes. If unset, the futures division
    # initializes as a stub.
    coinbase_futures_api_key: str | None
    coinbase_futures_api_secret: str | None
    coinbase_futures_passphrase: str | None
    # BitUnix Futures (Phase 1 read-only). No passphrase — BitUnix's auth
    # is SHA256-double-sign, not HMAC+passphrase. If unset, the broker
    # initializes as a stub returning $0 / no positions.
    bitunix_futures_api_key: str | None
    bitunix_futures_api_secret: str | None
    # Polymarket / Polygon — per-division wallets (item 6, 2026-05-29).
    # Each Polymarket division gets its own EOA (signer key + funder
    # address), keyed by division slug; see _POLYMARKET_WALLET_ENV for the
    # slug→env mapping. The RPC URL is SHARED across all divisions (one
    # Alchemy Polygon endpoint with an embedded API key). All pulled from KV
    # at runtime; never written to disk. A division with no/partial wallet →
    # the broker initializes as a stub.
    polymarket_wallets: dict[str, PolymarketWallet]
    polygon_rpc_url: str | None
    # Kalshi (Phase K1 — read-only). api_key_id is a UUID issued by Kalshi;
    # private_key_pem is the RSA PEM contents (multi-line — quote in .env or
    # store as multi-line KV secret). If unset, KalshiBroker initializes as
    # a stub returning $0 / no positions.
    kalshi_api_key_id: str | None
    kalshi_private_key_pem: str | None
    # Apify (Phase K3 — Kalshi Copy Trading). Token authorizes calls to
    # the saswave leaderboard + profile actors for whale discovery and
    # ongoing position monitoring. If unset, the Apify client initializes
    # in stub mode (returns empty results); the strategy no-ops safely.
    apify_api_token: str | None
    # Tastytrade (Phase: data provider live since 2026-05-22; Tasty Options
    # division shipping order placement on top). provider_secret is the OAuth
    # client secret; refresh_token is the long-lived grant — the SDK does NOT
    # self-rotate it (verified 2026-05-29 against the SDK source; only the
    # short-lived session/access token rotates). Both rotate manually per
    # `runbooks/tastytrade_oauth_rotation.md`. Both required for data (read)
    # and orders (write); if unset, TastytradeDataProvider and
    # TastytradeBroker initialize as stubs.
    #
    # PROD LOAD PATH: tastytrade creds load from
    # `/etc/trading-corp/tastytrade.env` (systemd EnvironmentFile drop-in),
    # NOT KV — there are NO `TASTYTRADE-*` secrets in KV today (verified
    # 2026-05-29). The runbook's Pre-flight 1 lists three possible load
    # paths; on this prod the EnvironmentFile is the ONLY live one.
    tastytrade_provider_secret: str | None
    tastytrade_refresh_token: str | None
    fidelity_username: str | None
    fidelity_password: str | None
    fidelity_account: str | None   # account name substring to filter, e.g. "Joint"
    db_url: str

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def _env(name: str, default: str | None = None) -> str | None:
    """Read an env var and strip whitespace.

    .env files are notoriously prone to invisible whitespace bugs — a stray
    space after `=` (e.g. `ANTHROPIC_API_KEY= sk-ant-...`) becomes part of
    the value and breaks API auth in non-obvious ways. We strip all leading
    and trailing whitespace from every secret on load. Returns None for
    empty-string values so `if secrets.X:` truthiness checks behave sanely.
    """
    raw = os.getenv(name, default)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def _populate_from_keyvault(vault_uri: str) -> None:
    """Pull every known secret from Azure Key Vault into os.environ.

    Used on Azure deployments. The VM has a system-assigned Managed
    Identity that's been granted "Key Vault Secrets User" role; the
    Azure SDK's DefaultAzureCredential picks that up automatically.

    Auth resolution order (DefaultAzureCredential):
      1. EnvironmentCredential   (CI/CD service principals, env vars)
      2. WorkloadIdentityCredential (AKS pod identity)
      3. ManagedIdentityCredential (Azure VMs, App Service) ← what we use
      4. AzureCliCredential       (local dev with `az login`)
      etc.

    Secret-name translation: Azure Key Vault doesn't allow underscores
    in secret names, so we translate `ANTHROPIC_API_KEY` ↔ `ANTHROPIC-API-KEY`.

    Existing env values (from .env or the process env) take precedence
    over KV — useful for one-off local overrides without touching the vault.
    Missing secrets in the vault are silently skipped (debug-logged) so
    bootstrapping works incrementally.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        log.warning(
            "KEY_VAULT_URI is set but azure-identity / azure-keyvault-secrets "
            "are not installed. Run: pip install azure-identity azure-keyvault-secrets"
        )
        return

    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_uri, credential=credential)
    except Exception as e:
        log.warning("Key Vault client init failed: %s", e)
        return

    # All env var names we want to populate from KV. Mirrors the
    # field set in `Secrets` plus a few config flags also stored in
    # the vault for centralized management.
    expected_env_vars = (
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ROBINHOOD_USERNAME",
        "ROBINHOOD_PASSWORD",
        "ROBINHOOD_MFA_SECRET",
        "COINBASE_API_KEY",
        "COINBASE_API_SECRET",
        "COINBASE_PASSPHRASE",
        "COINBASE_FUTURES_API_KEY",
        "COINBASE_FUTURES_API_SECRET",
        "COINBASE_FUTURES_PASSPHRASE",
        "BITUNIX_FUTURES_API_KEY",
        "BITUNIX_FUTURES_API_SECRET",
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_COPY_PRIVATE_KEY",
        "POLYMARKET_COPY_FUNDER_ADDRESS",
        "POLYGON_RPC_URL",
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY_PEM",
        "APIFY_API_TOKEN",
        "TASTYTRADE_PROVIDER_SECRET",
        "TASTYTRADE_REFRESH_TOKEN",
        "FIDELITY_USERNAME",
        "FIDELITY_PASSWORD",
        "FIDELITY_ACCOUNT",
        "TRADING_CORP_DB_URL",
        "LORD_OTTER_WEBHOOK_SECRET",
        "LORD_OTTER_DISABLE_IP_CHECK",
        "MARKET_CYPHER_WEBHOOK_SECRET",
        "MARKET_CYPHER_DISABLE_IP_CHECK",
        "ENABLE_TRADINGVIEW",
    )

    loaded = 0
    for env_name in expected_env_vars:
        if os.getenv(env_name):
            # Already set — env takes precedence so local overrides work
            continue
        kv_name = env_name.replace("_", "-")
        try:
            secret = client.get_secret(kv_name)
            if secret.value:
                os.environ[env_name] = secret.value
                loaded += 1
        except Exception as e:
            # Secret not in vault, or transient auth failure — degrade gracefully
            log.debug("Key Vault: skipped %s (%s): %s",
                      kv_name, type(e).__name__, str(e)[:120])

    log.info("Key Vault: loaded %d secrets from %s", loaded, vault_uri)


def load_secrets(env_file: Path | None = None) -> Secrets:
    """Load .env (if present) and return a Secrets object.

    NOTE: uses `override=True` so .env always wins over the existing process
    environment. Without this, a stale or empty value left in the parent
    shell (e.g. from a previous session that set ANTHROPIC_API_KEY="") will
    silently shadow a freshly-edited .env. We always want the file as the
    authoritative source.

    Azure mode: if `KEY_VAULT_URI` env var is set, also pull secrets from
    Azure Key Vault using the host's Managed Identity. KV is queried AFTER
    .env so .env values take precedence (useful for local debugging without
    touching prod KV). Missing-from-KV secrets degrade silently.
    """
    if env_file is None:
        env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=True)

    kv_uri = os.getenv("KEY_VAULT_URI")
    if kv_uri:
        _populate_from_keyvault(kv_uri)

    secrets = Secrets(
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        robinhood_username=_env("ROBINHOOD_USERNAME"),
        robinhood_password=_env("ROBINHOOD_PASSWORD"),
        robinhood_mfa_secret=_env("ROBINHOOD_MFA_SECRET"),
        coinbase_api_key=_env("COINBASE_API_KEY"),
        coinbase_api_secret=_env("COINBASE_API_SECRET"),
        coinbase_passphrase=_env("COINBASE_PASSPHRASE"),
        coinbase_futures_api_key=_env("COINBASE_FUTURES_API_KEY"),
        coinbase_futures_api_secret=_env("COINBASE_FUTURES_API_SECRET"),
        coinbase_futures_passphrase=_env("COINBASE_FUTURES_PASSPHRASE"),
        bitunix_futures_api_key=_env("BITUNIX_FUTURES_API_KEY"),
        bitunix_futures_api_secret=_env("BITUNIX_FUTURES_API_SECRET"),
        polymarket_wallets={
            slug: PolymarketWallet(
                private_key=_env(pk_env),
                funder_address=_env(fa_env),
            )
            for slug, (pk_env, fa_env) in _POLYMARKET_WALLET_ENV.items()
        },
        polygon_rpc_url=_env("POLYGON_RPC_URL"),
        kalshi_api_key_id=_env("KALSHI_API_KEY_ID"),
        kalshi_private_key_pem=_env("KALSHI_PRIVATE_KEY_PEM"),
        apify_api_token=_env("APIFY_API_TOKEN"),
        tastytrade_provider_secret=_env("TASTYTRADE_PROVIDER_SECRET"),
        tastytrade_refresh_token=_env("TASTYTRADE_REFRESH_TOKEN"),
        fidelity_username=_env("FIDELITY_USERNAME"),
        fidelity_password=_env("FIDELITY_PASSWORD"),
        fidelity_account=_env("FIDELITY_ACCOUNT"),
        db_url=_env("TRADING_CORP_DB_URL") or "sqlite:///data/trading_corp.db",
    )

    # Register loaded secret VALUES with the redaction filter so any log
    # output containing the raw value (including from third-party libs
    # that don't use our key-name conventions) is scrubbed. The KEY=value
    # pattern in `_REDACT_PATTERN` covers env-name-prefixed forms; this
    # covers everything else. Polymarket's private key is the load-bearing
    # case (py-clob-client signing-path DEBUG); we register the others
    # for defense-in-depth.
    # Per-division Polymarket wallets: register every wallet's private key
    # (load-bearing — py-clob-client signing-path DEBUG) and funder address
    # (public-info, but registering costs nothing and denies trivial
    # address-grepping from logs). RPC URL is shared.
    for _w in secrets.polymarket_wallets.values():
        register_redact_literal(_w.private_key)
        register_redact_literal(_w.funder_address)
    register_redact_literal(secrets.polygon_rpc_url)
    # Kalshi RSA private key — signs every Kalshi request. Same defense
    # as the polymarket wallet keys above.
    register_redact_literal(secrets.kalshi_private_key_pem)
    # Apify token — auth bearer for all saswave Kalshi actor calls. K3.
    register_redact_literal(secrets.apify_api_token)
    # Tastytrade OAuth secrets — both sensitive and BOTH long-lived (the SDK
    # does not self-rotate the refresh token; only the short-lived session
    # token rotates). Manual rotation only — see
    # `runbooks/tastytrade_oauth_rotation.md`.
    register_redact_literal(secrets.tastytrade_provider_secret)
    register_redact_literal(secrets.tastytrade_refresh_token)

    return secrets


def assert_live_ready(secrets: Secrets, brokers_required: tuple[str, ...]) -> None:
    """Raise RuntimeError if any required-for-LIVE credential is missing.

    `brokers_required` is e.g. ("robinhood", "coinbase").
    """
    if not secrets.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing — required for LIVE mode (and PAPER too).")
    missing: list[str] = []
    if "robinhood" in brokers_required:
        if not secrets.robinhood_username or not secrets.robinhood_password:
            missing.append("ROBINHOOD_USERNAME/PASSWORD")
    if "coinbase" in brokers_required:
        if not (secrets.coinbase_api_key and secrets.coinbase_api_secret):
            missing.append("COINBASE_API_KEY/SECRET")
    if "fidelity" in brokers_required:
        if not (secrets.fidelity_username and secrets.fidelity_password):
            missing.append("FIDELITY_USERNAME/PASSWORD")
    if "tastytrade" in brokers_required:
        if not (secrets.tastytrade_provider_secret and secrets.tastytrade_refresh_token):
            missing.append("TASTYTRADE_PROVIDER_SECRET/REFRESH_TOKEN")
    if "polymarket" in brokers_required:
        # Per-division wallets (item 6): a half-configured wallet (key XOR
        # funder) silently stubs the broker — fail loudly. Require at least
        # one COMPLETE wallet so a LIVE polymarket start isn't fully stubbed.
        # Scope is presence only — balance/allowance/MATIC/nonce preflight
        # belong to items 4/5, not here. NOTE: divisions aren't loaded at
        # preflight time (main.py:178 runs before load_divisions at :439), so
        # this validates wallet completeness + non-emptiness, not which
        # specific division is going live — the per-division auto_execute gate
        # is the finer control.
        complete = 0
        for slug, w in secrets.polymarket_wallets.items():
            if bool(w.private_key) != bool(w.funder_address):
                missing.append(
                    f"POLYMARKET wallet '{slug}' (key+funder must both be set)"
                )
            elif w.private_key and w.funder_address:
                complete += 1
        if complete == 0:
            missing.append("POLYMARKET (no division wallet has both key+funder set)")
    if missing:
        raise RuntimeError(
            "LIVE mode requested but missing credentials for: "
            + ", ".join(missing)
            + ". Set them in .env or run in PAPER mode."
        )


_REDACT_PATTERN = re.compile(
    r"(?P<key>" + "|".join(_SECRET_KEY_NAMES) + r")\s*=\s*[^\s,;}]+",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Replace secret-looking substrings with ***REDACTED***.

    Two passes:
      1. KEY=value pattern — catches env-style echoes (`POLYMARKET_PRIVATE_KEY=0xabc...`)
         regardless of whether the value has been loaded yet.
      2. Literal-value substitution — for any secret value registered via
         `register_redact_literal()` (called from `load_secrets()`), replace
         every occurrence with ***REDACTED*** regardless of context. This is
         the defense against third-party libraries that log raw secret values
         without our key-name conventions (py-clob-client signing path,
         web3.py provider URLs, etc.).
    """
    text = _REDACT_PATTERN.sub(lambda m: f"{m.group('key')}=***REDACTED***", text)
    if _REDACT_LITERALS:
        for literal in _REDACT_LITERALS:
            if literal in text:
                text = text.replace(literal, "***REDACTED***")
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secret values from emitted records."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a for a in record.args
                )
            except Exception:
                pass
        return True
