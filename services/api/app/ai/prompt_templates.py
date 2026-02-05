"""Inline fallback prompt templates.

These are used when no user override or bundled default is found.
Keep in sync with bundled templates in services/api/data/prompts/.
"""

from __future__ import annotations


# Inline fallback templates as raw strings
# These use Jinja2 variable syntax: {{variable_name}}

INLINE_DEFAULTS: dict[str, str] = {
    "image_analysis": """\
You are analyzing a personal lifelog photo captured by the user (the camera-holder).
Your goal is to extract structured "contexts" that help the user recall what THEY were doing and experiencing.
Be as concrete and detail-rich as possible without guessing.

## Perspective rules (important)
- Assume the user is present behind the camera.
- Write titles/summaries from the user's point of view (what the user is doing/experiencing).
- If an animal/object is the main subject, describe it as the user observing it (e.g. "Watching a dog resting" not "Dog is resting"),
  unless the user is clearly interacting.
- Do not invent names, relationships, locations, or emotions. If unsure, be generic.

## Context types (return 1-7 contexts; always include activity_context)
1) activity_context (REQUIRED): What the user is doing/experiencing at capture time.
2) social_context: Interaction(s) with other people (who + what).
3) location_context: Where the user is (place name/type). Prefer specific; otherwise generic (home, cafe, park, street).
4) food_context: Meal/drink/cooking details when visible.
5) emotion_context: The user's mood only if supported by evidence.
6) entity_context: People/places/objects that are salient.
7) knowledge_context: If the photo captures information for later (menu, sign, ticket, slide, book page), summarize what it says.

## Detail coverage rules
- Always include activity_context plus any other contexts that are clearly supported by evidence.
- If there are multiple distinct aspects (people, place, objects, text), include 3-6 contexts.
- In activity_context summary, include at least 2-4 concrete visual details (objects, clothing, signage, colors, textures, tools, gestures).
- When readable text exists (signs, menus, tickets), always add knowledge_context and include the text (use OCR if available).
- Prefer specific object/clothing details over generic terms like "outdoors" or "people".

## OCR text
If OCR text is provided below, use it to improve accuracy when text appears in the image.
OCR text (may be empty):
{{ocr_text}}

## Output format
Return JSON ONLY:
{
  "image_0": {
    "contexts": [
      {
        "context_type": "activity_context",
        "title": "Watching a dog rest on the sidewalk",
        "summary": "The user is outdoors and looking at a dog lying near a storefront.",
        "keywords": ["dog", "street", "outdoors"],
        "entities": [
          {"type": "object", "name": "dog", "confidence": 0.9}
        ],
        "location": {"name": "street"}
      }
    ]
  }
}

## Field guidelines
- title: 5-12 words, specific.
- summary: 2-4 sentences, factual, user-centric, and detail-rich.
- keywords: 5-12 short lowercase phrases; favor concrete nouns/adjectives over generic terms.
- entities: list of {type: person|place|object|org|food|topic, name: "...", confidence: 0..1}.
- location: optional {name, lat, lng}; only include lat/lng if explicitly known.

{{language_guidance}}
""",

    "video_chunk": """\
You are analyzing a chunk of a personal video captured by the user (the camera-holder).
Return a verbatim transcript of spoken words (if any) plus 1-5 structured contexts.

## Context taxonomy (use these when applicable)
### 1. activity_context (REQUIRED for every chunk)
What the user is doing/experiencing. Categories:
- Daily routines: sleeping, eating, cooking, cleaning
- Work/learning: working, studying, reading, writing
- Physical: exercising, walking, running, sports
- Social: chatting, meeting, socializing, calling
- Entertainment: watching_tv, gaming, listening_music
- Creative: drawing, photography, crafting
- Other: commuting, shopping, traveling, relaxing

### 2. social_context (if interaction with others detected)
- Who is involved (names if known, otherwise "person_1", "person_2")
- Nature of interaction (conversation, activity together)
- Setting

### 3. location_context (if identifiable location)
- Place name/type (home, cafe, park, gym, street)
- Notable features if visible

### 4. food_context (if food/meal visible)
- Meal type (breakfast, lunch, dinner, snack)
- Food items or cooking details

### 5. emotion_context (if the user's mood is evident)
- Emotional state with supporting cues (happy, focused, relaxed, energetic)

### 6. entity_context (if people, places, objects, orgs, or topics are salient)
- People/places/objects/organizations/topics that matter in the scene
- Format: {type: "person|place|object|org|food|topic", name: "...", confidence: 0-1}

### 7. knowledge_context (if the chunk contains information to remember)
- Summarize factual info (signs, menus, tickets, announcements, instructions, schedules)

Return JSON ONLY with this shape:
{
  "transcript": "...",
  "contexts": [
    {
      "context_type": "activity_context",
      "title": "...",
      "summary": "...",
      "keywords": ["..."],
      "entities": [{"type": "person|place|object|org|food|topic", "name": "...", "confidence": 0.0}],
      "location": {"name": "...", "lat": 0.0, "lng": 0.0}
    }
  ]
}

Perspective rules:
- Assume the user is present behind the camera.
- Write titles/summaries from the user's point of view (what the user is doing/experiencing).
- Do not invent names, relationships, locations, or emotions. If unsure, be generic.

Transcript rules:
- If there is no intelligible speech, return "".
- Keep it verbatim (you may use markers like [inaudible], [laughter]).
- Do not add timestamps.

Context rules:
- Always include at least one activity_context.
- Add other context types when applicable: social_context, location_context, food_context, emotion_context, entity_context, knowledge_context.
- Keep contexts short and non-duplicative (merge closely related observations).
- Summaries must be factual; do not invent details.

{{language_guidance}}
""",

    "audio_chunk": """\
You are analyzing a chunk of a personal audio recording captured by the user (the recorder).
Return a verbatim transcript (if any) plus 1-5 structured contexts.

## Context taxonomy (use these when applicable)
### 1. activity_context (REQUIRED for every chunk)
What the user is doing/experiencing. Categories:
- Daily routines: sleeping, eating, cooking, cleaning
- Work/learning: working, studying, reading, writing
- Physical: exercising, walking, running, sports
- Social: chatting, meeting, socializing, calling
- Entertainment: watching_tv, gaming, listening_music
- Creative: drawing, photography, crafting
- Other: commuting, shopping, traveling, relaxing

### 2. social_context (if interaction with others detected)
- Who is involved (names if known, otherwise "person_1", "person_2")
- Nature of interaction (conversation, activity together)
- Setting if mentioned

### 3. location_context (if identifiable location is mentioned)
- Place name/type (home, cafe, park, gym, street)
- Only include if explicitly stated

### 4. food_context (if food/meal is discussed)
- Meal type (breakfast, lunch, dinner, snack)
- Food items or cooking details

### 5. emotion_context (if the user's mood is evident)
- Emotional state with supporting cues (happy, focused, relaxed, energetic)

### 6. entity_context (if people, places, objects, orgs, or topics are salient)
- People/places/objects/organizations/topics that matter in the audio
- Format: {type: "person|place|object|org|food|topic", name: "...", confidence: 0-1}

### 7. knowledge_context (if the audio contains information to remember)
- Summarize factual info (instructions, plans, meeting notes, reminders, schedules)

Return JSON ONLY with this shape:
{
  "transcript": "...",
  "contexts": [
    {
      "context_type": "activity_context",
      "title": "...",
      "summary": "...",
      "keywords": ["..."],
      "entities": [{"type": "person|place|object|org|food|topic", "name": "...", "confidence": 0.0}],
      "location": {"name": "...", "lat": 0.0, "lng": 0.0}
    }
  ]
}

Perspective rules:
- Assume the user is present at the recording.
- Write titles/summaries from the user's point of view (what the user is doing/experiencing).
- Do not invent names, relationships, locations, or emotions. If unsure, be generic.

Transcript rules:
- If there is no intelligible speech, return "".
- Keep it verbatim (you may use markers like [inaudible], [laughter], [music]).
- Do not add timestamps.

Context rules:
- Always include at least one activity_context.
- Add other context types when applicable: social_context, location_context, food_context, emotion_context, entity_context, knowledge_context.
- Keep contexts short and non-duplicative (merge closely related observations).
- Summaries must be factual; do not invent details.

{{language_guidance}}
""",

    "transcription": """\
You are transcribing personal lifelog {{media_kind}}.

Return the verbatim transcript as plain text. Do not add commentary, speaker labels, or timestamps.

{{language_guidance}}
""",

    "episode_summary": """\
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
""",

    "chat_system": """\
You are a personal memory assistant. Answer the user's questions using the provided memories.

Guidelines:
- Be warm, clear, and helpful.
- Use only the provided context. If you are unsure, say you do not have enough information.
- If relevant memories are provided, do NOT say you lack information; answer using those memories even if coverage is partial.
- When a resolved time range is provided, use it exactly and do not infer dates from memory recency or absence.
- When referencing a memory, mention the date/time and the source index in brackets (e.g., [1]).
- For day/week/month recap questions, provide a short summary paragraph plus 3-6 bullet points of key moments.
- Aim for 5-8 sentences total unless the user asks for less.
- Do not end mid-sentence; always finish your thought.
- If the user asks multiple questions, answer each in order.
- Use first-person perspective when referencing the user's memories.
""",

    "query_entities": """\
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
""",

    "date_range": """\
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
- If no date range is implied, return nulls.

Return JSON ONLY with this exact shape:
{
  "start_date": "YYYY-MM-DD" | null,
  "end_date": "YYYY-MM-DD" | null
}

Query:
{{query}}
""",

    "session_title": """\
Create a concise 6-10 word title for a chat session.
- Capture the full request.
- Include dates or places if present.

Request:
"{{first_message}}"
Return plain text only.
""",

    "cartoon_agent": """\
You are an art director creating a single cartoon illustration.
Return JSON ONLY with this exact shape:
{
  "image_prompt": "",
  "caption": ""
}

Guidelines:
- image_prompt should be 80-140 words, vivid, concrete, and mention "cartoon illustration".
- Describe the setting, time of day, main characters (no names), 3-6 key actions/props, mood, lighting, color palette, and camera/composition.
- If the date label is a range, blend 2-3 highlights into one cohesive scene (not a collage).
- caption should be 12-20 words and read like a playful title card.
- Use at least 5 concrete details from Memory context; copy short phrases when possible.
- Only use details present in Memory context. Do not invent events, props, or signage.
- Never add date labels, day panels, or calendar text unless it explicitly appears in Memory context.
- If you include any date text, it must be from Available memory dates below.
- If details are missing, keep the scene warm and generic without inventing specifics.

Date: {{date_label}}
Available memory dates: {{available_dates}}

User instruction:
{{instruction}}

Memory context:
{{memory_context}}
""",

    "day_insights_agent": """\
You are an analyst and visual designer creating a daily memory insights summary.
Return JSON ONLY with this exact shape:
{
  "headline": "",
  "summary": "",
  "top_keywords": [],
  "labels": [],
  "image_prompt": ""
}

Guidelines:
- headline: 6-12 words that name the day or range.
- summary: 3-5 sentences, user-centric and factual; mention 2-3 concrete moments and 2-3 stats from Stats JSON.
- top_keywords: 8-12 lowercase keywords.
- labels: 4-7 short labels describing dominant themes.
- image_prompt: create a clean infographic poster; include the headline and 4-6 stat callouts with numbers and short text blocks.
- Use at least 4 concrete details from Memory context in summary or labels.
- Only use details present in Memory context. Do not invent events, props, or signage.
- If you include any date text, it must be from Available memory dates below.
- If details are missing, keep things generic and avoid guessing.

Date range: {{date_range_label}}
Available memory dates: {{available_dates}}

User instruction:
{{instruction}}

Stats JSON:
{{stats_json}}

Memory context:
{{memory_context}}
""",

    "surprise_agent": """\
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
""",

    # Agent prompts for Memory Agent mode
    "agent_system": """\
You are the OmniMemory Agent, helping users manage their personal memories.

## Capabilities
- Search and filter memories by query, date, people, places
- Create and modify episodes (grouped memories)
- Add annotations, tags, and descriptions
- Generate insights and summaries
- Organize and categorize memories

## Conversation Style
- Be conversational and helpful
- Offer relevant suggestions after each response
- Remember context from the conversation
- Ask clarifying questions when needed

## Current Session State
{{session_state}}
""",

    "agent_action": """\
Given the conversation history and user message, determine the action to take.

Actions:
- search: Find memories matching criteria
- filter: Narrow current results
- summarize: Generate summary of selection
- update: Modify memory metadata
- create_episode: Group memories into episode
- insights: Generate insights about patterns
- clarify: Ask user for more information

Conversation history:
{{conversation_history}}

User message:
{{message}}

Respond with JSON ONLY:
{
  "action": "...",
  "parameters": {...},
  "reasoning": "..."
}
""",

    "agent_response": """\
Generate a helpful response based on the action result.

Conversation history:
{{conversation_history}}

Action result:
{{action_result}}

Session state:
{{session_state}}

Guidelines:
- Summarize what was found or done
- Offer relevant follow-up suggestions
- Be conversational but concise
- If there are pending write actions, mention they need confirmation
""",

    "query_intent": """\
Classify the user's query into one of these intents:

**meta_question**: Question about current date/time, system stats, or capabilities. PRIORITIZE this if the query asks about date/time.
- Examples: "what is today's date", "hi what is today's date", "what day is it"

**greeting**: Simple greeting WITHOUT any question
- Examples: "hi", "hello", "good morning"

**clarification**: Follow-up about a previous response
- Examples: "tell me more", "what do you mean"

**memory_query**: User wants to recall or search their memories (DEFAULT)
- Examples: "what did I do yesterday", "show me photos from Paris"

Return JSON ONLY:
{"intent": "memory_query" | "meta_question" | "greeting" | "clarification"}

Query:
{{query}}
""",

    "rerank": """\
Given the user's query and candidate memories, return the indices of the most relevant memories in order of relevance.

Query: {{query}}

Candidates:
{{candidates}}

Return JSON array of indices (0-indexed), most relevant first:
{"ranking": [0, 3, 1, ...]}
""",
}
