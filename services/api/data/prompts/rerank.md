---
name: rerank
version: "1.0.0"
description: Rerank memory candidates by relevance to query
output_format: json
required_vars:
  - query
  - candidates
---

Given the user's query and a list of memory candidates, rank them by relevance.

Consider:
1. **Semantic relevance**: How well does the memory match the user's intent?
2. **Temporal relevance**: If the query mentions a time period, prioritize memories from that period.
3. **Entity relevance**: If the query mentions people, places, or things, prioritize memories containing those entities.
4. **Context completeness**: Prefer episode summaries over individual items when they provide better context.

User Query:
{{query}}

Memory Candidates (indexed from 0):
{{candidates}}

Return a JSON object with the indices of the most relevant memories in order of relevance (most relevant first).
Only include indices of memories that are genuinely relevant to the query.
If a memory is not relevant, do not include its index.

Return JSON ONLY with this exact shape:
{"ranking": [0, 3, 1, ...]}
