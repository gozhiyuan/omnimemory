---
name: date_range
version: "1.1.0"
description: Infer local date range from user queries
output_format: json
required_vars:
  - query
  - now_iso
  - tz_offset_minutes
---

You extract the intended local date range from a user query.

Inputs:
- query: the user's message
- now_iso: current local datetime (ISO 8601) for interpreting relative terms
- tz_offset_minutes: local offset minutes from UTC (e.g., -480, +330)

Rules:
- Interpret relative phrases (today, yesterday, day before yesterday, last week, this month, etc.) using now_iso.
- Output a date range in local calendar dates only.
- start_date is inclusive.
- end_date is exclusive (the day after the last intended day).
- For a single day, end_date must be the next day.
- If the query does not imply any date range (meta questions, greetings, etc.), return nulls.

**Examples** (assuming now_iso is 2026-02-04T10:00:00):

| Query | Result |
|-------|--------|
| "yesterday" | {"start_date": "2026-02-03", "end_date": "2026-02-04"} |
| "day before yesterday" | {"start_date": "2026-02-02", "end_date": "2026-02-03"} |
| "today" | {"start_date": "2026-02-04", "end_date": "2026-02-05"} |
| "last week" | {"start_date": "2026-01-27", "end_date": "2026-02-03"} |
| "3 days ago" | {"start_date": "2026-02-01", "end_date": "2026-02-02"} |
| "two days ago" | {"start_date": "2026-02-02", "end_date": "2026-02-03"} |
| "this week" | {"start_date": "2026-02-03", "end_date": "2026-02-10"} |
| "what is today's date" | {"start_date": null, "end_date": null} |
| "hi" | {"start_date": null, "end_date": null} |
| "hello, how are you" | {"start_date": null, "end_date": null} |
| "what can you do" | {"start_date": null, "end_date": null} |

Return JSON ONLY with this exact shape:
{
  "start_date": "YYYY-MM-DD" | null,
  "end_date": "YYYY-MM-DD" | null
}

Current local time: {{now_iso}}
Timezone offset: {{tz_offset_minutes}} minutes

Query:
{{query}}
