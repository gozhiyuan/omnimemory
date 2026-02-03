---
name: session_title
version: "1.0.0"
description: Generate chat session title
output_format: text
required_vars:
  - first_message
---

Create a concise 6-10 word title for a chat session.
- Capture the full request.
- Include dates or places if present.

Request:
"{{first_message}}"
Return plain text only.
