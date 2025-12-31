"""Prompt templates for VLM analysis and transcription."""

from __future__ import annotations


OCR_TEXT_PLACEHOLDER = "<<OCR_TEXT>>"

LIFELOG_IMAGE_ANALYSIS_V2_PROMPT = """\
You are analyzing a personal lifelog photo captured by the user (the camera-holder).
Your goal is to extract structured "contexts" that help the user recall what THEY were doing and experiencing.

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

## OCR text
If OCR text is provided below, use it to improve accuracy when text appears in the image.
OCR text (may be empty):
{OCR_TEXT_PLACEHOLDER}

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
- summary: 1-3 sentences, factual, user-centric.
- keywords: 3-12 short lowercase phrases.
- entities: list of {type: person|place|object|org|food|topic, name: "...", confidence: 0..1}.
- location: optional {name, lat, lng}; only include lat/lng if explicitly known.
"""


def build_lifelog_image_prompt(ocr_text: str | None) -> str:
    cleaned = (ocr_text or "").strip()
    if len(cleaned) > 2000:
        cleaned = cleaned[:2000] + "..."
    return LIFELOG_IMAGE_ANALYSIS_V2_PROMPT.replace(OCR_TEXT_PLACEHOLDER, cleaned or "None")


LIFELOG_TRANSCRIPTION_PROMPT = """\
You are transcribing personal lifelog audio.

Return the verbatim transcript as plain text. Do not add commentary, speaker labels, or timestamps.
"""


def build_lifelog_transcription_prompt(media_kind: str) -> str:
    kind = (media_kind or "audio").strip().lower()
    if kind not in {"audio", "video"}:
        kind = "audio"
    return f"{LIFELOG_TRANSCRIPTION_PROMPT}\nMedia type: {kind}."


LIFELOG_VIDEO_CHUNK_PROMPT = """\
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
"""


def build_lifelog_video_chunk_prompt() -> str:
    return LIFELOG_VIDEO_CHUNK_PROMPT


LIFELOG_AUDIO_CHUNK_PROMPT = """\
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
"""


def build_lifelog_audio_chunk_prompt() -> str:
    return LIFELOG_AUDIO_CHUNK_PROMPT
