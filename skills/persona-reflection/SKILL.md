---
name: persona-reflection
description: Review real conversations, evaluate user feedback on trialed styles, and convert repeated evidence into structured personality conclusions across Hermes's fixed dimensions. Use when running reflection, adaptation review, and personality summarization workflows.
---

## Purpose

Determine what Hermes actually learned from interaction, and whether the user liked the content, the expression, or both.

## Required Inputs

- Recent conversation history
- `PERSONALITY_FEED.md` style samples
- Current `SOUL.md`
- Existing pending signals if present

## Operating Rules

1. Review which style samples were actually used.
2. Distinguish:
   - content approval
   - expression approval
   - task success
3. Evaluate signals using:
   - explicit feedback
   - behavioral response
   - repetition across contexts
   - follow-through in user interaction
4. Assign adaptation outcomes such as:
   - `confirmed_fit`
   - `probable_fit`
   - `contextual_fit`
   - `unclear`
   - `misfit`
5. Do not treat one smooth interaction as stable preference.
6. Convert repeated evidence into personality architecture summaries.
7. Map all summaries into the fixed six dimensions:
   - expression style
   - emotional temperature
   - judgment posture
   - relationship distance
   - interests and aesthetics
   - core boundaries
8. If more than two dimensions are strongly hit, prepare priority ordering and pending candidates.

## Output Contract

Update `PERSONALITY_FEED.md` under `Style Adaptation`, and write structured summaries for governance review.

Each reflection result should include:

- `sample_id`
- `feedback_result`
- `evidence_summary`
- `liked_content_or_expression`
- `repetition_note`
- `dimension_hits`
- `candidate_personality_conclusion`

## Forbidden Moves

- Never dump raw user feedback directly into `SOUL.md`
- Never confuse performance with fit
- Never modify core boundaries automatically

## Review Checklist

Before finishing, confirm:

- You separated task success from style fit
- You mapped conclusions into the fixed dimensions
- You identified any overflow candidates that should become `pending`
