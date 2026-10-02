# ADR-0013 — Assistant model chosen after the Sprint 8 evaluation

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D13

## Context
Candidate models and list prices (per million tokens, input/output): Claude Opus 5.5 (`claude-opus-5-5`, $4/$20), Claude Sonnet 5.5 (`claude-sonnet-5-5`, $2/$10), Claude Haiku 4.5 (`claude-haiku-4-5`, $1/$5). Estimated assistant cost at about 6k input tokens per turn (mostly cached) and 400 output tokens: roughly $15, $8 or $4 per week at 50 demo users; roughly $150, $75 or $40 per week at 500 pilot users. Mixed-language understanding and multi-step tool use matter more than raw cost, but the budget is small.

## Decision
- Build and test the assistant against **Claude Opus 5.5** first.
- In Sprint 8, run the **200-utterance English/Hinglish/Marathi evaluation set** on Opus 5.5, Sonnet 5.5 and Haiku 4.5.
- Choose the model with the best accuracy-per-cost that meets the quality bar (intent accuracy and slot-filling F1 thresholds agreed in the eval protocol).
- Tool definitions use `strict: true` schemas and `tool_choice: auto` (current models reject forced tool choice); prompt caching on system prompt and tools; per-user daily caps; refusal fallback enabled.

## Alternatives considered
- **Fix a model now** — rejected: no measured data yet.

## Consequences
- (+) Evidence-based model choice that also makes a good paper/viva point.
- (−) The eval set must be labelled before S8 (Manish Raje).

## Related
[ADR-0005](0005-text-assistant-mvp-voice-stretch.md) · Handbook [Chapter 08](../handbook/08-ai-and-ml.md)
