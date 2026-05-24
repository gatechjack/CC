"""Sports-market EV-at-fill math.

Venue-agnostic helpers for evaluating two-leg cross-venue arbs and
one-leg directional bets between Kalshi and US sportsbooks. Pure
functions; no I/O.

Load-bearing per the 2026-05-22 [[kalshi-crypto-shelved]] post-mortem:
EV-at-fill — not WR, not gross divergence — is the only discriminating
metric. Every observer/strategy must compute it from line one. This
module is the canonical implementation.

Fee citation: Kalshi taker fee is `f = ceil(0.07 × C × P × (1−P) × 100) / 100`
per fill (Kalshi fee schedule, https://kalshi.com/docs/fees). Round
direction is UP — venue rounds in its favor.

US sportsbook moneyline / spread / total prices are quoted in American
odds. Decimal payout multiplier `D` = 1 + (α/100) for α≥+100, or
1 + (100/|α|) for α≤−100. Implied raw probability (with vig) = 1/D.
Vig-removed prob is the caller's responsibility (median across books or
normalization across both sides).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ── Fee ──────────────────────────────────────────────────────────────────

def kalshi_fee(contracts: float, price: float) -> float:
    """Kalshi taker fee per fill, rounded UP to next cent.

    `contracts` is positive (number of YES or NO contracts filled).
    `price` is the per-contract price in dollars, [0, 1].
    """
    if contracts <= 0 or not (0.0 <= price <= 1.0):
        return 0.0
    raw = 0.07 * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0) / 100.0


# ── American-odds helpers ────────────────────────────────────────────────

def american_to_decimal(american: int) -> float:
    """Decimal payout multiplier (gross, including stake return)."""
    if american >= 100:
        return 1.0 + american / 100.0
    if american <= -100:
        return 1.0 + 100.0 / abs(american)
    raise ValueError(f"American odds in (-100, +100) are invalid: {american}")


def american_to_implied_raw(american: int) -> float:
    """Vig-INCLUDED implied probability from a single American quote.

    For vig-removed B-leadlag model_prob, the caller must combine
    multiple books or both sides of a market.
    """
    return 1.0 / american_to_decimal(american)


# ── Leg / result dataclasses ─────────────────────────────────────────────

@dataclass(frozen=True)
class LegFill:
    """One leg of a fill, expressed as $1-unit normalized.

    The convention: each leg is sized so that, if it wins, it pays
    exactly `qty` dollars gross (Kalshi: qty contracts × $1; sportsbook:
    stake $qty/decimal pays $qty if it wins). This makes A-arb math
    straightforward — two opposing legs sized to the same `qty` produce
    a guaranteed $qty payoff with cost = sum(leg.cost_total).

    Fields:
      venue:               "kalshi" | book_key (e.g., "draftkings")
      side:                Kalshi: "yes"|"no". Book: "home"|"away"|"over"|"under".
      qty:                 number of $1-payoff units bought on this leg.
      price_per_unit:      what you pay per $1 of "if-wins" payoff,
                           BEFORE fees. Kalshi: yes_ask or no_ask in
                           dollars. Book: 1/decimal_odds.
      fee:                 absolute dollars of fees on this leg. Kalshi:
                           kalshi_fee(qty, price). Book: 0.0 (US books
                           don't charge moneyline commission).
    """
    venue: str
    side: str
    qty: float
    price_per_unit: float
    fee: float = 0.0

    @property
    def cost_total(self) -> float:
        """Total dollars paid for this leg, fees included."""
        return self.qty * self.price_per_unit + self.fee

    @property
    def payoff_if_wins(self) -> float:
        """Gross dollars received if this leg wins."""
        return self.qty


@dataclass(frozen=True)
class EVResult:
    """Output of an EV-at-fill computation."""
    ev_dollars: float            # signed; >0 means profitable in expectation
    cost_paid: float             # total cost across all legs, fees included
    expected_payoff: float       # E[gross payoff] — for A, equals payoff_min
    is_arb: bool                 # True iff guaranteed (worst-case payoff > cost)
    hypothesis: str              # "A_arb" | "B_leadlag"
    breakdown: dict = field(default_factory=dict)


# ── EV-at-fill computations ──────────────────────────────────────────────

def compute_ev_at_fill_a_arb(kalshi_leg: LegFill, book_leg: LegFill) -> EVResult:
    """Two-leg arb. The legs must be on OPPOSING outcomes of the same
    event (caller's responsibility). Both sized to the same `qty`.

    Arb iff guaranteed-min-payoff > total-cost. The min-payoff under
    same-qty sizing equals `qty` (one leg always pays, the other zero).
    Negative `ev_dollars` means the "arb" loses both-sides after fees —
    common at tiny-capital sizing where the Kalshi fee is a meaningful
    fraction of the spread.
    """
    if kalshi_leg.venue != "kalshi":
        raise ValueError(f"kalshi_leg must have venue='kalshi', got {kalshi_leg.venue!r}")
    if book_leg.venue == "kalshi":
        raise ValueError("book_leg cannot also be venue='kalshi'")
    if abs(kalshi_leg.qty - book_leg.qty) > 1e-9:
        raise ValueError(
            f"A-arb requires equal qty on both legs; got "
            f"kalshi={kalshi_leg.qty}, book={book_leg.qty}"
        )

    cost_total = kalshi_leg.cost_total + book_leg.cost_total
    payoff_min = kalshi_leg.qty
    ev = payoff_min - cost_total
    return EVResult(
        ev_dollars=round(ev, 4),
        cost_paid=round(cost_total, 4),
        expected_payoff=round(payoff_min, 4),
        is_arb=ev > 0,
        hypothesis="A_arb",
        breakdown={
            "kalshi": {
                "side": kalshi_leg.side,
                "qty": kalshi_leg.qty,
                "price_per_unit": kalshi_leg.price_per_unit,
                "fee": kalshi_leg.fee,
                "cost_total": round(kalshi_leg.cost_total, 4),
            },
            "book": {
                "venue": book_leg.venue,
                "side": book_leg.side,
                "qty": book_leg.qty,
                "price_per_unit": book_leg.price_per_unit,
                "fee": book_leg.fee,
                "cost_total": round(book_leg.cost_total, 4),
            },
        },
    )


def compute_ev_at_fill_b_directional(
    kalshi_leg: LegFill, model_prob_outcome: float,
) -> EVResult:
    """One-leg directional bet using a sharp-book-implied model_prob.

    `model_prob_outcome` is P(this leg's side wins) per the sharp-book
    proxy. EV = qty × (model_prob × 1.0 − price_per_unit) − fee.

    A null result here (mean EV ≤ 0 across the corpus) is only
    interpretable in light of where `model_prob_outcome` came from —
    Pinnacle-clean is a real B test; median(DK/FD/BetMGM) is a
    soft-book proxy and the verdict report must flag that caveat.
    """
    if kalshi_leg.venue != "kalshi":
        raise ValueError(f"B-leg must be venue='kalshi', got {kalshi_leg.venue!r}")
    if not (0.0 <= model_prob_outcome <= 1.0):
        raise ValueError(f"model_prob_outcome must be in [0,1], got {model_prob_outcome}")

    expected_payoff = kalshi_leg.qty * model_prob_outcome
    cost_total = kalshi_leg.cost_total
    ev = expected_payoff - cost_total
    return EVResult(
        ev_dollars=round(ev, 4),
        cost_paid=round(cost_total, 4),
        expected_payoff=round(expected_payoff, 4),
        is_arb=False,                # never a guaranteed arb in B
        hypothesis="B_leadlag",
        breakdown={
            "kalshi": {
                "side": kalshi_leg.side,
                "qty": kalshi_leg.qty,
                "price_per_unit": kalshi_leg.price_per_unit,
                "fee": kalshi_leg.fee,
                "cost_total": round(cost_total, 4),
            },
            "model_prob_outcome": model_prob_outcome,
        },
    )
