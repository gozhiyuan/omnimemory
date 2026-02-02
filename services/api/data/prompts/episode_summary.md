---
name: episode_summary
version: "1.0.0"
description: Generate episode-level summary from multiple items
output_format: json
required_vars:
  - items_json
  - item_count
  - time_range
  - language_guidance
optional_vars:
  - omitted_count
---

You are summarizing a personal lifelog episode for the user (the camera-holder).
Use the provided per-item summaries and context fields to produce ONE episode-level title and summary.

Episode info:
- item_count: {{item_count}}
- time_range: {{time_range}}
- omitted_items: {{omitted_count}}

Items (JSON):
{{items_json}}

Output JSON ONLY:
{
  "title": "...",
  "summary": "...",
  "keywords": ["..."]
}

Guidelines:
- title: 5-12 words, user-centric (what the user was doing/experiencing).
- summary: 2-4 sentences, factual, cover the full episode; mention shifts if multiple activities.
- keywords: 5-12 lowercase phrases that capture the episode.
- Do not invent names, relationships, locations, or emotions not present in the items.
- If inputs are sparse, be generic but accurate.

{{language_guidance}}
