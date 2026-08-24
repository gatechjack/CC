"""READ-ONLY: distinguish (a) stale in-memory key vs (b) order-path defect for poly_kalshi's 401.

poly_kalshi_mlb signs with the KALSHI-KAREN key. The live broker materializes its PEM to a /tmp tempfile
for the process lifetime (KalshiBroker.connect). We HASH that tempfile and compare to the CURRENT vault
value BY HASH -- the engine's ACTUAL loaded key vs vault, without touching process memory.
  match  -> engine holds the current key -> (b) ORDER-PATH defect (restart pointless).
  differ -> engine holds a stale/rotated key -> (a) STALE key (restart reloads current).
SECRETS NEVER PRINTED -- only a truncated sha256 fingerprint + match/differ + KV metadata timestamps.
Fallback if the tempfile is unreadable (PrivateTmp): compare vault KALSHI-KAREN updated_on vs engine start.
"""
import glob
import hashlib
import os


def _sha16(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]  # truncated fingerprint (non-reversible), never the value


def main():
    uri = os.environ.get("KEY_VAULT_URI")
    start = os.environ.get("ENGINE_START", "?")
    print("engine start (ExecMainStartTimestamp):", start)
    if not uri:
        print("KEY_VAULT_URI not set -- cannot reach vault. STOP."); return
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    client = SecretClient(vault_url=uri, credential=DefaultAzureCredential())

    print("\n== VAULT key fingerprints + last-updated (metadata only) ==")
    vault = {}
    for label, name in (("KALSHI", "KALSHI-PRIVATE-KEY-PEM"),
                        ("KALSHI-KAREN", "KALSHI-KAREN-PRIVATE-KEY-PEM"),
                        ("Karen-TWO", "Karen-Kalshi-TWO-PRIVATE-KEY")):
        try:
            s = client.get_secret(name)
            fp = _sha16((s.value or "").strip().encode())
            vault[label] = fp
            upd = getattr(s.properties, "updated_on", None)
            print("  %-13s pem_sha256[:16]=%s  updated_on=%s  version=%s"
                  % (label, fp, upd, (s.properties.version or "")[:8]))
        except Exception as e:  # noqa: BLE001
            vault[label] = None
            print("  %-13s FETCH FAILED: %s: %s" % (label, type(e).__name__, str(e)[:100]))

    print("\n== ENGINE materialized PEM tempfiles (its ACTUAL loaded keys) ==")
    paths = sorted(set(glob.glob("/tmp/kalshi_*.pem")
                       + glob.glob("/tmp/systemd-private-*trading-corp*/tmp/kalshi_*.pem")))
    if not paths:
        print("  (none visible to this user)")
    readable = 0
    engine_fps = []
    for f in paths:
        try:
            with open(f, "rb") as fh:
                fp = _sha16(fh.read().strip())
            readable += 1
            engine_fps.append(fp)
            match = [lbl for lbl, vh in vault.items() if vh and vh == fp]
            print("  %-52s sha256[:16]=%s  matches_vault=%s" % (f, fp, match or "NONE"))
        except Exception as e:  # noqa: BLE001
            print("  %-52s UNREADABLE: %s" % (f, type(e).__name__))

    print("\n== VERDICT ==")
    kk = vault.get("KALSHI-KAREN")
    if readable == 0:
        print("  UNVERIFIED (direct): no engine PEM tempfile readable (PrivateTmp isolation likely).")
        print("  FALLBACK -> compare vault KALSHI-KAREN updated_on (above) to engine start (%s):" % start)
        print("    updated_on AFTER start  -> vault rotated since boot -> engine likely STALE -> (a).")
        print("    updated_on BEFORE start -> engine loaded current value -> (b) order-path defect.")
    elif kk and kk in engine_fps:
        print("  MATCH: an engine PEM tempfile == the CURRENT vault KALSHI-KAREN.")
        print("  -> The engine HOLDS THE CURRENT KEY. This is (b) ORDER-PATH DEFECT.")
        print("     A restart would NOT help; the fix is in the order-signing path, not the credential.")
    else:
        print("  DIFFER: NO engine PEM tempfile matches the current vault KALSHI-KAREN.")
        print("  -> The engine's loaded KALSHI-KAREN differs from current vault. This is (a) STALE KEY.")
        print("     A restart would reload the current vault value (schedule deliberately).")
    print("\n== END keydiag ==")


main()
