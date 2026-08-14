import xml.etree.ElementTree as ET

def failset(path):
    fails, errs = set(), set()
    for tc in ET.parse(path).getroot().iter('testcase'):
        cid = (tc.get('classname') or '') + '::' + (tc.get('name') or '')
        if tc.find('failure') is not None:
            fails.add(cid)
        if tc.find('error') is not None:
            errs.add(cid)
    return fails, errs

base_f, base_e = failset(r"C:\Users\AA Incorporado\cc\mace_golive_preflight_junit.xml")
new_f, new_e = failset(r"C:\Users\AA Incorporado\cc\mace_oq2_phase4_junit.xml")

print("BASE (golive preflight 2026-08-11): fails=%d errors=%d" % (len(base_f), len(base_e)))
print("NEW  (oq2 phase4 2026-08-13):       fails=%d errors=%d" % (len(new_f), len(new_e)))
print()
print("=== NEW failures NOT in base (the +delta) ===")
for x in sorted(new_f - base_f):
    print("  +", x)
print("=== base failures NO LONGER failing in new ===")
for x in sorted(base_f - new_f):
    print("  -", x)
print("=== error-set delta ===")
for x in sorted(new_e - base_e):
    print("  +E", x)
for x in sorted(base_e - new_e):
    print("  -E", x)
print()
print("MACE check: new failures touching mace =",
      sorted(x for x in new_f | new_e if 'mace' in x.lower()))
