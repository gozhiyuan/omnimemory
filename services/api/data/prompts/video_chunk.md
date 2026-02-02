---
name: video_chunk
version: "1.0.0"
description: Analyze video segment for transcript and contexts
output_format: json
required_vars:
  - language_guidance
---

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
