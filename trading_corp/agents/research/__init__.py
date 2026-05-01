"""Research firm — shared service that produces typed research products
on behalf of other divisions (v3).

Architectural model: NOT a division. No book, no orders directly placed,
no broker, no `auto_execute` of its own. Stateless from its own
perspective; receives EngagementSpec, returns typed product. Per-division
code owns when/whether/which/what to ask, and what to do with the answer.

See planning/research_firm_design.md.

Public entry point: `agents.research.engagement.run_engagement(spec, deps)`.

Phase 1a-1 emits `CandidateRecommendation` only. Other product types
(`Thesis`, `PositionContext`, `TradeConfirmation`) land in subsequent
phases (1b / 1d / 1e respectively).
"""
