"""Fee-drag recompute (read-only arithmetic) — net-per-fire under modeled vs actual fees.

Fee-drag in R = round_trip_fraction / stop_fraction   (since entry/risk_per_unit = 1/stop%).
The recent cost test gave gross/fire ~ +0.063R, win-rate ~62%, on a 0.30% stop. Here we
recompute net-per-fire under each fee scenario to show how much drag is fee-model
overstatement vs a genuine stop/edge problem.

Rates: published VIP3 taker 0.0400% / maker 0.0140%; ACTUAL blended entry ~0.0243% (Fee
Discount Card, N=5 live trades); ACTUAL exit roles CONFIRMED exact (TP=maker 0.0140%,
SL=taker 0.0400%). Slippage kept at the model's 0.005%/leg (backtest gross has no slippage).
INTERIM: one bear/quiet window; N=5 for the actual entry rate (inconsistent 0.0195-0.0366%,
min-fee artifact possible). Accounting check, not a statistical one.
"""
GROSS = 0.063          # cost-test gross/fire (R)
WIN = 0.62             # win rate -> TP(maker) exit; loss -> SL(taker) exit
STOP = 0.003           # 0.30% stop
SLIP2 = 2 * 0.00005    # round-trip slippage (model)
TK, MK = 0.0004, 0.00014          # published VIP3
ENTRY_ACT = 0.000243              # actual blended entry (Fee Discount Card, ~39% off taker)

def drag(rt):  # round-trip fraction -> R drag
    return rt / STOP

def blended_exit(winrate):  # TP=maker, SL=taker
    return winrate * MK + (1 - winrate) * TK

SCEN = {
    "(d) all-taker  [model headline _RT_TK]": TK + TK + SLIP2,
    "    model maker-exit  [_RT_MK]":         TK + MK + SLIP2,
    "(b) ACTUAL-effective [disc entry+blend]": ENTRY_ACT + blended_exit(WIN) + SLIP2,
    "(c) all-maker  [B2 entry ON, both maker]": MK + MK + SLIP2,
}

print(f"gross/fire=+{GROSS}R  stop={STOP*100:.2f}%  win={WIN:.0%}  (fee-drag R = round_trip% / stop%)")
print(f"{'scenario':<42} {'round_trip%':>11} {'drag(R)':>9} {'net/fire(R)':>12}")
for name, rt in SCEN.items():
    d = drag(rt)
    print(f"{name:<42} {rt*100:>10.4f}% {d:>9.3f} {GROSS - d:>+12.3f}")
print()
print(f"recoverable by fee-CORRECTNESS (taker-headline -> actual-effective): "
      f"{drag(TK+TK+SLIP2) - drag(ENTRY_ACT+blended_exit(WIN)+SLIP2):+.3f}R")
print(f"further by all-maker entry (B2) vs actual-effective: "
      f"{drag(ENTRY_ACT+blended_exit(WIN)+SLIP2) - drag(MK+MK+SLIP2):+.3f}R")
print(f"best-case (all-maker) net/fire: {GROSS - drag(MK+MK+SLIP2):+.3f}R  "
      f"(gross +{GROSS} < all-maker drag {drag(MK+MK+SLIP2):.3f} -> stop/edge problem remains)")
