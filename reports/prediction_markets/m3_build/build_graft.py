import hashlib, os, py_compile, sys, tempfile

ST = os.path.dirname(os.path.abspath(__file__))
box = open(os.path.join(ST, "box_main.py"), "rb").read()
block = open(os.path.join(ST, "m3_block.txt"), "rb").read().replace(b"\r\n", b"\n").replace(b"\r", b"\n")

def sha16(b):
    return hashlib.sha256(b).hexdigest()[:16]

def crstrip(b):
    return b.replace(b"\r", b"")

print("== inputs ==")
print("  box bytes=%d raw_sha16=%s cr_sha16=%s" % (len(box), sha16(box), sha16(crstrip(box))))
assert sha16(crstrip(box)) == "236a6be054268278", "box base drift"
print("  block bytes=%d lines=%d ends_nl=%s has_CR=%s" % (len(block), block.count(b"\n"), block.endswith(b"\n"), b"\r" in block))
assert block.endswith(b"\n") and b"\r" not in block

# anchor: the roster_split comment line; insert M3 block + one blank line BEFORE it
anchor = b"        # --- Phase 2a boot invariant:"
n = box.count(anchor)
print("  anchor occurrences =", n)
assert n == 1, "anchor not unique"
pos = box.find(anchor)
# pos is start of the anchor line (line begins right after the preceding \n)
assert box[pos-1:pos] == b"\n", "anchor not at line start"

grafted = box[:pos] + block + b"\n" + box[pos:]

# prove ONLY the insertion changed (byte-level, nothing else touched)
assert grafted[:pos] == box[:pos]
assert grafted[pos + len(block) + 1:] == box[pos:]
print("== splice proof: bytes before anchor unchanged AND bytes from anchor onward unchanged -> ONLY the block+blank inserted ==")

tgt_raw, tgt_cr = sha16(grafted), sha16(crstrip(grafted))
print("== grafted ==")
print("  grafted bytes=%d raw_sha16=%s cr_sha16=%s (TARGET)" % (len(grafted), tgt_raw, tgt_cr))
print("  size delta = +%d bytes (block %d + 1 blank)" % (len(grafted) - len(box), len(block)))

def cnt(hay, needle):
    return hay.count(needle.encode())

print("== MACE / base markers: grafted vs box (must be EQUAL) ==")
for m in ["dxfeed", "tastytrade", "mace", "KalshiTailPriceArbAgent", "Poly->Kalshi MLB copy WIRED"]:
    b_, g_ = cnt(box, m), cnt(grafted, m)
    print("  %-28s box=%d grafted=%d %s" % (m, b_, g_, "OK" if b_ == g_ else "*** CHANGED ***"))
    assert b_ == g_, "MACE marker changed: " + m

print("== PM driver markers: grafted vs box (must be EQUAL; driver untouched) ==")
for m in ["scheduled_pm_live_loop", "plan_driver_tasks", "PM LIVE DRIVER WIRED", "active_driver_subdivisions"]:
    b_, g_ = cnt(box, m), cnt(grafted, m)
    print("  %-28s box=%d grafted=%d %s" % (m, b_, g_, "OK" if b_ == g_ else "*** CHANGED ***"))
    assert b_ == g_

print("== M3 markers: box (absent) -> grafted (present) ==")
for m, exp in [("scheduled_shard_snapshot_loop", 1), ("M3 shard-snapshot writer WIRED", 1),
               ("shard_snapshot_task_handle", 1), ("SHARD-BALANCE SNAPSHOTS", 1)]:
    b_, g_ = cnt(box, m), cnt(grafted, m)
    print("  %-32s box=%d grafted=%d (expect %d)" % (m, b_, g_, exp))
    assert b_ == 0 and g_ == exp
pm_box, pm_g = cnt(box, "trading_corp.prediction_markets"), cnt(grafted, "trading_corp.prediction_markets")
print("  %-32s box=%d grafted=%d (expect 1 -> 2)" % ("prediction_markets imports", pm_box, pm_g))
assert pm_box == 1 and pm_g == 2

open(os.path.join(ST, "grafted_main.py"), "wb").write(grafted)

# local syntax gate
tmp = os.path.join(tempfile.gettempdir(), "m3_grafted_check.py")
open(tmp, "wb").write(grafted)
try:
    py_compile.compile(tmp, doraise=True)
    print("== local py_compile OK ==")
except py_compile.PyCompileError as e:
    print("*** local py_compile FAILED ***"); print(e); sys.exit(1)

# base64 of the LF block for the apply runner (box-side splice)
import base64
b64 = base64.b64encode(block).decode()
open(os.path.join(ST, "m3_block.b64"), "w").write(b64)
print("== wrote grafted_main.py + m3_block.b64 (chars=%d) ==" % len(b64))
print("TARGET_CR_SHA16=%s" % tgt_cr)
print("TARGET_RAW_SHA16=%s" % tgt_raw)
print("BLOCK_LF_SHA16=%s" % sha16(block))
