"""Compaction boundary marker constant.

This prefix is prepended to user messages that serve as compaction
boundaries in session transcripts.  _build_messages splits at the last
such marker: earlier history is not included in the LLM context, but
the marker message itself IS included so the LLM sees the summary.
"""

COMPACTION_MARKER_PREFIX = "-- COMPACTION_SUMMARY --\n"
