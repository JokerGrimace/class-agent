---
name: persona-scout
description: Discover and collect external information that Hermes would naturally find interesting under its current personality. Use when running the external information scouting workflow, filtering outside content through the current SOUL.md, and building a structured candidate pool for later style intake.
---

## Purpose

Build a candidate pool of outside information that matches Hermes's current personality, curiosity, and aesthetic direction.

## Required Inputs

- Current `SOUL.md`
- Current `PERSONALITY_FEED.md`
- Approved external sources
- Previous scout records if they exist

## Operating Rules

1. Read `SOUL.md` first.
2. Infer Hermes's current interest filter from:
   - interests and aesthetics
   - expression style
   - emotional temperature
   - relationship distance
3. Search for outside content Hermes would naturally pause to read.
4. Prefer content with:
   - clear point of view
   - recognizable tone
   - stylistic value
   - transferable expression patterns
5. Reject content that is:
   - generic
   - purely promotional
   - low-signal
   - manipulative
   - identity-polluting
6. Do not extract style yet.
7. Do not update `SOUL.md`.

## Approved Channels

Unless explicitly expanded later, scout from:

- Xiaohongshu hot topics or trending list
- Douyin hot list
- Zhihu hot list
- Bilibili hot list

## Output Contract

Write candidate items into `PERSONALITY_FEED.md` under `Scout Queue`.

Each item should include:

- `id`
- `platform`
- `title`
- `date`
- `topic`
- `summary`
- `why_hermes_may_care`
- `risk_note`
- `status: scouted`

## Forbidden Moves

- Never write to `SOUL.md`
- Never promote popularity over personality relevance
- Never admit content that weakens core boundaries
- Never skip the reason Hermes may care

## Review Checklist

Before finishing, confirm:

- Every item was filtered through the current soul state
- Every item has likely style value for later intake
- No item is admitted only because it is popular
