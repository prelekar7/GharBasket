# ADR-0005 — Assistant scope: text MVP with tool calls; voice as a stretch goal

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D5

## Context
Seniors and home-makers may prefer talking to the app in Hinglish or Marathi ("kal 2 kilo aloo extra bhejna"). A conversational assistant is valuable but must not become the riskiest part of the project. Voice adds speech-to-text evaluation (Sarvam AI, Bhashini, Whisper) and a labelled audio test set.

## Decision
- Build a **text assistant ("Ghar Saathi" chat)** in Sprint 8 using the Claude API with tool calling over our own API, acting **as the user**: `get_my_week`, `edit_box_line`, `skip_week`, `book_tiffin`, `track_order`, `explain_item`.
- Every write returns a **proposed change** the user confirms in the UI. The LLM never computes prices or quantities.
- Prompt caching for system prompt and tools; per-user daily token caps; PII-scrubbed logs.
- Evaluate on 200 labelled English/Hinglish/Marathi utterances (intent accuracy, slot-filling F1).
- **Voice input is a stretch goal**, built only if milestones are on track.

## Alternatives considered
- **Full voice in core scope** — rejected: extra evaluation and device work for a two-coder team.
- **No assistant** — rejected: loses a useful senior-friendly channel and a strong demo moment.

## Consequences
- (+) Low-risk, demonstrable LLM feature with clear safety boundaries.
- (−) Ongoing API cost; capped and measured.

## Related
[ADR-0013](0013-assistant-model-chosen-by-eval.md) · Handbook [Chapter 08](../handbook/08-ai-and-ml.md)
