set -u
# READ-ONLY: show ONLY the newest Authelia filesystem-notifier block (the enrolment/reset
# link most recently generated). Tails the last block; never writes; never restarts authelia.
#
# ============================ TWO GOTCHAS -- READ BEFORE RELAYING ============================
# GOTCHA 1 -- A NEW LINK COMES FROM THE PERSON RETRYING THE REGISTRATION, NOT FROM RE-RUNNING
#   THIS READER. This runner only READS notification.txt. Re-running it shows the SAME block
#   (which may already be STALE/EXPIRED) until the person triggers a FRESH enrolment attempt in
#   their browser. If the timestamp/expiry below is old, have them retry FIRST, then re-run.
# GOTCHA 2 -- default_2fa_method is still 'totp' on the box, so the login screen offers the
#   AUTHENTICATOR (TOTP) option ALONGSIDE the passkey option. The person MUST pick the
#   security-key / passkey option, not the authenticator. (Make passkey the default with
#   `default_2fa_method: webauthn` + restart authelia -- NOT done; a separate one-line change.)
# ============================================================================================
F=/var/lib/authelia/notification.txt
echo "### AUTHELIA NEWEST ENROLMENT LINK (READ-ONLY as root via az; nothing written; authelia NOT restarted) ###"
echo "### now(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)  -- compare this against the entry TIMESTAMP below to confirm the link is FRESH ###"

if [ ! -f "$F" ]; then
  echo "  NOTE: $F does not exist yet -- no notification has been generated."
  echo "  Have the person start their passkey enrolment, then re-run this runner."
  echo "### END (file absent) ###"
  exit 0
fi

echo "  file: $F"
echo "  perms=$(stat -c '%a %U:%G' "$F" 2>/dev/null)  size=$(stat -c '%s' "$F" 2>/dev/null)B  mtime(UTC)=$(date -u -r "$F" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"

N=$(grep -cE '^Date:' "$F" 2>/dev/null)
echo "  total notification blocks in file (by 'Date:' anchor): ${N:-0}  (file accumulates as people re-enrol; this shows only the LAST one)"

echo
echo "=== NEWEST BLOCK (verbatim -- from the LAST 'Date:' line to end of file; this is the ground truth) ==="
if [ "${N:-0}" -ge 1 ]; then
  BLOCK=$(awk '/^Date:/{b=$0 ORS; next} {b=b $0 ORS} END{printf "%s", b}' "$F")
else
  echo "  (no 'Date:' anchor found -- Authelia format may differ; showing last 40 lines as a fallback)"
  BLOCK=$(tail -n 40 "$F")
fi
printf '%s\n' "$BLOCK" | sed 's/^/  | /'

echo
echo "=== AT-A-GLANCE (parsed from the newest block above) ==="
echo "  TIMESTAMP  : $(printf '%s\n' "$BLOCK" | grep -m1 -E '^Date:' | sed 's/^Date:[[:space:]]*//')"
echo "  FOR (user) : $(printf '%s\n' "$BLOCK" | grep -m1 -iE '^Recipient:' | sed 's/^[Rr]ecipient:[[:space:]]*//')"
echo "  SUBJECT    : $(printf '%s\n' "$BLOCK" | grep -m1 -iE '^Subject:' | sed 's/^[Ss]ubject:[[:space:]]*//')"
echo "  LINK(S)    :"
LINKS=$(printf '%s\n' "$BLOCK" | grep -oE 'https?://[^[:space:]]+' | sed -E 's/[.,;>)]+$//' | sort -u)
if [ -n "$LINKS" ]; then
  printf '%s\n' "$LINKS" | sed 's/^/     /'
else
  echo "     (no URL in this block -- Authelia may have sent a one-time CODE instead; read the verbatim block above)"
fi

echo
echo "=== EXPIRY (these links are one-time + time-limited -- do NOT relay a stale one) ==="
EXP=$(printf '%s\n' "$BLOCK" | grep -iE 'expir|valid for|valid until|one.?time|single.use|[0-9]+ *(minute|hour|day)')
if [ -n "$EXP" ]; then
  echo "  plain-text expiry wording found in the block:"
  printf '%s\n' "$EXP" | sed 's/^/    /'
else
  echo "  (no plain-text expiry wording in the block)"
fi
# Best-effort: if the link carries a JWT token=, decode its 'exp' claim for an absolute expiry.
TOK=$(printf '%s\n' "$BLOCK" | grep -oE '[?&]token=[A-Za-z0-9_.-]+' | head -1 | sed 's/^[?&]token=//')
if [ -n "$TOK" ]; then
  PAY=$(printf '%s' "$TOK" | cut -d. -f2 | tr '_-' '/+')
  case $(( ${#PAY} % 4 )) in 2) PAY="${PAY}==";; 3) PAY="${PAY}=";; esac
  EXPTS=$(printf '%s' "$PAY" | base64 -d 2>/dev/null | grep -oE '"exp":[0-9]+' | grep -oE '[0-9]+' | head -1)
  if [ -n "$EXPTS" ]; then
    echo "  token 'exp' claim: $EXPTS = $(date -u -d @"$EXPTS" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) UTC (single-use link, expires then)"
    NOWTS=$(date -u +%s)
    if [ "$EXPTS" -le "$NOWTS" ]; then
      echo "  ** WARNING: this token is ALREADY EXPIRED -- have the person re-request, then re-run. **"
    else
      echo "  remaining validity: ~$(( (EXPTS - NOWTS) / 60 )) min from now"
    fi
  fi
fi
echo "### END (nothing changed; authelia untouched) ###"
