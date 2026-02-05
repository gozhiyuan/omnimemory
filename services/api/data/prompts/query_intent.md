---
name: query_intent
version: "1.1.0"
description: Classify user query intent for routing
output_format: json
required_vars:
  - query
---

Classify the user's query into one of these intents:

**meta_question**: Question about current date/time, system stats, or capabilities. PRIORITIZE this if the query asks about date/time even if combined with other elements.
- Examples: "what is today's date", "what day is it", "what can you do", "how many memories do I have"
- Also includes: "hi what is today's date", "hello, what's the current time", "hey what day is today"

**greeting**: Simple greeting or casual conversation starter WITHOUT any question
- Examples: "hi", "hello", "hey there", "good morning", "how are you"
- NOT greeting if includes a question about memories or date

**clarification**: Follow-up about a previous response or request for more detail
- Examples: "tell me more", "what do you mean", "can you explain", "which one"

**memory_query**: User wants to recall, search, or explore their memories
- Examples: "what did I do yesterday", "show me photos from Paris", "when was my trip to Kyoto", "find memories with coffee"
- This is the DEFAULT if the query doesn't clearly fit the above categories

**Priority order when query has multiple elements:**
1. meta_question (if asking about date/time/capabilities)
2. clarification (if clearly asking about previous response)
3. memory_query (if asking about memories/activities)
4. greeting (only if no other intent present)

Return JSON ONLY with this exact shape:
{"intent": "memory_query" | "meta_question" | "greeting" | "clarification"}

Query:
{{query}}
