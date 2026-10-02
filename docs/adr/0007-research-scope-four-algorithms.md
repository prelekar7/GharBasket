# ADR-0007 — Research scope: one systems paper covering all four algorithms

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D7

## Context
GharBasket builds four algorithmic components with research value: the pantry digital twin (hierarchical Bayesian consumption model), the CP-SAT meal planner, fairness-aware kitchen assignment, and uncommitted-demand forecasting with newsvendor procurement. The team chose to include all four in the research output. Evaluating all four in equal depth is heavy for one paper.

## Decision
- One **systems paper** presenting GharBasket's architecture and all four algorithms.
- **Deepest experiments** (baselines, ablations, metrics): the CP-SAT meal planner and the pantry digital twin, because they power the headline Kitchen Autopilot USP.
- Fair assignment and forecasting/procurement are evaluated with standard metrics (earnings Gini and Pareto curve; WAPE, MASE, pinball loss, waste per 100 kg).
- An optional second short paper on fair assignment and forecasting if time allows.
- Results on synthetic data and on pilot data are reported **separately**.
- All references must be verified by hand; placeholder references from earlier drafts are removed.

## Alternatives considered
- **Two or three algorithms only** — rejected by the team, which wants all four represented.
- **Four equally deep studies** — rejected: not feasible alongside the build.

## Consequences
- (+) Covers both the AI and social-impact halves of the project.
- (−) Evaluation harness must be built early (Sprint 7) to produce paper figures in time.

## Related
[ADR-0008](0008-simulator-first-data-source-tagging.md) · Handbook [Chapter 08](../handbook/08-ai-and-ml.md)
