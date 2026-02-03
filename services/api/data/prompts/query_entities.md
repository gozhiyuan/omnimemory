---
name: query_entities
version: "1.0.0"
description: Extract entity names from user queries
output_format: json
required_vars:
  - query
---

Extract entity names from the user query below.
Return JSON ONLY with this exact shape:
{
  "people": [],
  "places": [],
  "objects": [],
  "organizations": [],
  "topics": [],
  "food": []
}

Query:
{{query}}
