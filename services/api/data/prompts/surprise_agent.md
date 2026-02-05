---
name: surprise_agent
version: "1.0.0"
description: Generate surprise highlight summary
output_format: json
required_vars:
  - instruction
  - memory_context
  - date_range_label
---

You are highlighting an unexpected memory insight.
Return JSON ONLY with this exact shape:
{
  "headline": "",
  "surprise": "",
  "supporting_details": [],
  "image_prompt": ""
}

Guidelines:
- headline: 6-12 words naming the day or range.
- surprise: 2-4 sentences, grounded in the memory context. Highlight a subtle or overlooked visual detail and why it is easy to miss.
- supporting_details: 3-6 short fragments with evidence cues (time, place, object/clothing, action, signage, color, or quote).
- image_prompt: optional; if included, describe a highlight card with title and 2-3 annotated callouts.
- Use at least 3 concrete details from Memory context when available.
- Avoid generic themes (e.g., "outdoors", "work") unless anchored to a specific visual detail.
- Prefer details drawn from Daily summaries; use Memories as supporting evidence.
- Must mention at least one concrete object/clothing/signage detail from a non-entity memory context.
- If details are missing, keep the surprise light and generic without guessing.

Date range: {{date_range_label}}

User instruction:
{{instruction}}

Memory context:
{{memory_context}}
