"""Decode the JWT body of a Tastytrade refresh token and print its scope claim.

Verification check for the Tastytrade OAuth rotation runbook
(`runbooks/tastytrade_oauth_rotation.md` Check 5). Confirms that a freshly-
granted refresh token actually carries the scopes that were requested at
grant time — the gotcha being that Tastytrade silently downgrades the
granted scope to whatever the OAuth app config permits, with no error.

Token sources, in order of preference (most to least history-safe):
    1. `--env-var NAME` (default `TASTYTRADE_REFRESH_TOKEN`) — read from
       the process environment. The token never appears in argv, `ps`,
       shell history, or this script's output.
    2. `--stdin` — read one line from stdin. Useful for `... | python
       scripts/check_tt_token_scope.py --stdin` one-off pipelines. The
       token does not appear in argv.

Token is NEVER accepted as a command-line argument — that form lands in
`ps` and shell history and is precisely the leak surface the rotation
runbook exists to close.

Output: prints `scope:` and `exp:` lines only. Never echoes the token.
Fails closed: any missing / malformed / unparseable input — or a JWT
that decodes successfully but is missing the `scope` or `exp` claims
this runbook depends on — exits non-zero with a stderr message. The
script will NOT exit 0 with `scope: None` or any empty/garbage output;
Check 5 reading a false green off a silently-passing verification tool
is worse than having no tool.

Exit codes:
    0 — successful decode; non-empty `scope` and integer `exp` printed
    2 — token not found in the requested source (empty / unset)
    3 — value present but not a parseable JWT body (wrong shape, bad
        base64, non-JSON middle segment)
    4 — JWT decoded successfully but missing required claims
        (`scope` absent or non-string-or-empty; `exp` absent or
        non-integer)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from typing import Any


def _decode_jwt_body(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"Not a JWT: expected 3 dot-separated parts, got {len(parts)}"
        )
    body = parts[1]
    pad = "=" * (-len(body) % 4)
    raw = base64.urlsafe_b64decode(body + pad)
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Print the scope claim of a Tastytrade refresh token JWT. "
            "Never accepts the token as a CLI argument — use --env-var "
            "or --stdin so the value stays out of `ps`, history, and logs."
        ),
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--env-var",
        default="TASTYTRADE_REFRESH_TOKEN",
        metavar="NAME",
        help=(
            "Env-var name to read the token from "
            "(default: TASTYTRADE_REFRESH_TOKEN). Never pass the token "
            "value as an argument."
        ),
    )
    src.add_argument(
        "--stdin",
        action="store_true",
        help="Read one line from stdin instead of an env-var.",
    )
    args = p.parse_args(argv)

    if args.stdin:
        token = sys.stdin.readline().strip()
        source_desc = "stdin"
    else:
        token = os.environ.get(args.env_var, "").strip()
        source_desc = f"env var '{args.env_var}'"

    if not token:
        print(
            f"ERROR: token not found (source: {source_desc} is unset or empty)",
            file=sys.stderr,
        )
        return 2

    try:
        body = _decode_jwt_body(token)
    except Exception as exc:
        # Echo the exception class + message, NOT the token itself.
        print(f"ERROR: failed to decode JWT body ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 3

    # Fail-closed validation of the two claims the runbook's Check 5
    # depends on. Without these guards, a JWT missing `scope` or `exp`
    # would print "scope: None" / "exp: None" and exit 0 — a false
    # green that defeats the purpose of the verification check.
    scope = body.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        print(
            "ERROR: JWT decoded but `scope` claim is missing or empty. "
            f"Got: {type(scope).__name__}. Token is not usable for scope "
            "verification - re-run the OAuth grant (runbook Step 1).",
            file=sys.stderr,
        )
        return 4

    exp = body.get("exp")
    if not isinstance(exp, int):
        print(
            "ERROR: JWT decoded but `exp` claim is missing or non-integer. "
            f"Got: {type(exp).__name__}. Token is malformed — re-run the "
            "OAuth grant (runbook Step 1).",
            file=sys.stderr,
        )
        return 4

    # Print only the claims that the runbook's Check 5 cares about.
    # Never print the raw JWT or any value that might re-leak the token.
    print(f"scope: {scope}")
    print(f"exp:   {exp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
