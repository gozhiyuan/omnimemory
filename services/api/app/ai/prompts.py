"""Prompt templates for VLM analysis."""

from __future__ import annotations


OCR_TEXT_PLACEHOLDER = "<<OCR_TEXT>>"

LIFELOG_IMAGE_ANALYSIS_V1_PROMPT = """\
You are analyzing personal life photos to extract meaningful memories.

For EACH image, extract ALL applicable contexts. Always create at least one activity_context.

## Context Types:

### 1. activity_context (REQUIRED for every image)
Extract what the person is doing. Categories:
- Daily routines: sleeping, eating, cooking, cleaning
- Work/learning: working, studying, reading, writing
- Physical: exercising, walking, running, sports
- Social: chatting, meeting, socializing, calling
- Entertainment: watching_tv, gaming, listening_music
- Creative: drawing, photography, crafting
- Other: commuting, shopping, traveling, relaxing

### 2. entity_context (if people, places, or significant objects visible)
Extract:
- People: Names if recognizable (or "person_1", "person_2")
- Places: Restaurants, parks, landmarks, rooms
- Objects: Notable items (books, equipment, food items)

Format: {type: "person|place|object", name: "...", confidence: 0-1}

### 3. social_context (if interaction with others detected)
- Who is involved
- Nature of interaction (conversation, activity together)
- Setting

### 4. food_context (if food/meal visible)
- Meal type (breakfast, lunch, dinner, snack)
- Food items visible
- Location (home, restaurant)

### 5. location_context (if identifiable location)
- Place name
- Place type (home, cafe, park, gym)
- Notable features

### 6. emotion_context (if mood/feeling detectable)
- Emotional state visible (happy, focused, relaxed, energetic)
- Context clues

## OCR Text
If OCR text is provided below, use it to improve accuracy when text appears in the image.
OCR text (may be empty):
{OCR_TEXT_PLACEHOLDER}

## Output Format:

Return JSON ONLY:
{
  "image_0": {
    "contexts": [
      {
        "context_type": "activity_context",
        "title": "Morning run in Central Park",
        "summary": "Jogging on the main trail, sunny morning, wearing blue running jacket and headphones",
        "activity_category": "running",
        "entities": [
          {"type": "place", "name": "Central Park", "confidence": 0.9},
          {"type": "object", "name": "running shoes", "confidence": 0.95}
        ],
        "importance": 0.6,
        "confidence": 0.9
      }
    ]
  }
}

## Importance Scoring:
- 0.9-1.0: Special occasions (birthdays, weddings, major achievements)
- 0.7-0.9: Significant social activities, memorable experiences
- 0.5-0.7: Regular but meaningful activities (meals with others, hobbies)
- 0.3-0.5: Routine daily activities
- 0.1-0.3: Mundane moments

## Guidelines:
- Be specific but concise
- Use present tense for descriptions
- Don't invent details not visible
- Extract ALL relevant contexts (multiple per image is normal)
- Rate confidence honestly (0.5 = uncertain, 0.9 = very certain)
"""


def build_lifelog_image_prompt(ocr_text: str | None) -> str:
    cleaned = (ocr_text or "").strip()
    if len(cleaned) > 2000:
        cleaned = cleaned[:2000] + "..."
    return LIFELOG_IMAGE_ANALYSIS_V1_PROMPT.replace(OCR_TEXT_PLACEHOLDER, cleaned or "None")
