# Lifelog AI - Updated PRD Sections (MineContext-Inspired)

## Section 4: Enhanced Architecture Deep Dive

### 4.1 Data Layer Architecture (UPDATED)

#### Multi-Context Extraction Strategy

**Key Insight from MineContext**: A single piece of media can generate multiple orthogonal contexts.

**Implementation for Lifelog**:
```
One photo of "cooking dinner with friends" generates:
├─ activity_context: "Cooking Italian dinner"
├─ social_context: "Evening with Alice and Bob"
├─ food_context: "Homemade pasta carbonara"
├─ location_context: "Home kitchen"
└─ entity_context: "Alice", "Bob", "pasta maker", "red wine"
```

**Benefits**:
- Richer retrieval surface (more entry points to same memory)
- Better answers to diverse query types
- Natural support for faceted search ("all meals with Alice" vs "all cooking activities")

#### Processing Pipeline Architecture

```python
# Inspired by MineContext's processor pattern

class MediaProcessorManager:
    """Routes media to appropriate processors"""
    
    def __init__(self):
        self.processors = {
            "photo": ImageProcessor(),
            "video": VideoProcessor(),
            "audio": AudioProcessor(),
        }
    
    async def process(self, source_item: SourceItem) -> List[ProcessedContext]:
        processor = self.processors[source_item.item_type]
        return await processor.process(source_item)

class ImageProcessor:
    """
    Processes images in batches, extracts multiple context types.
    Adapted from MineContext's ScreenshotProcessor.
    """
    
    async def process(self, source_item: SourceItem) -> List[ProcessedContext]:
        # 1. Add to batch queue (accumulate for efficiency)
        await self.batch_queue.put(source_item)
        
        # 2. Batch worker processes every 30s or when batch size reaches 10
        # 3. Single VLM call analyzes entire batch
        # 4. Returns 1-N contexts per image
        # 5. Semantic merging across time windows
        # 6. Entity extraction and graph update
        # 7. Embedding generation
        # 8. Qdrant upsert
```

**Batch Processing Flow**:
```
10 photos uploaded at 2pm
    ↓
Batch queue (wait 30s or size=10)
    ↓
Single VLM call with 10 images
    ↓
Extract contexts (2-4 per image = 20-40 total contexts)
    ↓
Semantic merge (group by type + 2hr window)
    ↓
Store in Qdrant (maybe 15 contexts after merging)
```

### 4.2 Memory Layer Architecture (UPDATED)

#### Context Type Taxonomy

```python
# Based on MineContext's context types, adapted for life logging

class LifelogContextType(str, Enum):
    # Core memory types
    ACTIVITY = "activity_context"      # What user was doing
    ENTITY = "entity_context"          # People, places, objects (like MineContext)
    SOCIAL = "social_context"          # Social interactions
    LOCATION = "location_context"      # Place-based memories
    
    # Life-specific types
    FOOD = "food_context"              # Meals, restaurants, cooking
    EMOTION = "emotion_context"        # Mood, feelings
    HEALTH = "health_context"          # Exercise, sleep (from wearables)
    
    # Document-based (like MineContext)
    KNOWLEDGE = "knowledge_context"    # Notes, documents (chunked)
```

#### Semantic Merging Strategy

**Problem**: 30 photos of "cooking dinner" over 90 minutes should become ONE coherent event, not 30 separate memories.

**Solution** (adapted from MineContext's batch merging):

```python
async def semantic_merge(contexts: List[ProcessedContext]) -> List[ProcessedContext]:
    """
    Merge similar contexts within time windows.
    
    Example:
    Input: [
      {type: activity, title: "Chopping vegetables", time: 6:00pm},
      {type: activity, title: "Boiling pasta", time: 6:15pm},
      {type: activity, title: "Making sauce", time: 6:30pm},
      {type: food, title: "Pasta ingredients", time: 6:00pm},
      {type: food, title: "Finished carbonara", time: 6:45pm}
    ]
    
    Output: [
      {
        type: activity,
        title: "Cooking Italian dinner",
        summary: "Prepared pasta carbonara from scratch over 90 minutes...",
        duration_minutes: 90,
        importance: 0.8,
        merged_from_ids: [ctx1.id, ctx2.id, ctx3.id]
      },
      {
        type: food,
        title: "Homemade pasta carbonara",
        summary: "Made with eggs, bacon, parmesan...",
        merged_from_ids: [ctx4.id, ctx5.id]
      }
    ]
    """
    
    # 1. Group by context_type and 2-hour time window
    groups = group_by_type_and_time(contexts, window_hours=2)
    
    merged = []
    for group in groups:
        if len(group) == 1:
            merged.append(group[0])
            continue
        
        # 2. Use LLM to merge
        merge_prompt = f"""
        These {len(group)} contexts are from the same 2-hour window:
        {json.dumps([ctx.dict() for ctx in group])}
        
        Decide whether to:
        - Merge them into one richer context (if same continuous activity)
        - Keep separate (if distinct activities)
        
        For merged contexts, provide:
        - Enriched title and summary
        - Combined entities
        - Total duration
        - Max importance score
        """
        
        merge_result = await llm.chat(merge_prompt, response_format="json")
        
        # 3. Process merge decisions
        for decision in merge_result["decisions"]:
            if decision["merge_type"] == "merged":
                merged_ctx = create_merged_context(
                    contexts=[group[i] for i in decision["indices"]],
                    merged_data=decision["data"]
                )
                merged.append(merged_ctx)
            else:
                merged.extend([group[i] for i in decision["indices"]])
    
    return merged
```

### 4.3 RAG Chat System Architecture (UPDATED)

#### Tool-Based Retrieval (Inspired by MineContext ContextAgent)

**Key Insight**: Instead of one big vector search, use **multiple specialized retrieval tools** and let the LLM plan which to use.

**Implementation**:

```python
class LifelogChatAgent:
    """
    Multi-stage chat pipeline inspired by MineContext's ContextAgent:
    1. Intent classification
    2. Context retrieval (tool planning + execution)
    3. Response generation
    """
    
    async def chat(self, user_id: str, message: str):
        # Stage 1: Classify intent
        intent = await self.classify_intent(message)
        # Returns: simple_chat | qa_analysis | stats_query | timeline_query
        
        if intent == "simple_chat":
            return await self.simple_response(message)
        
        # Stage 2: Context retrieval (up to 2 rounds, 3-5 tools per round)
        contexts = []
        for round in range(2):
            # LLM plans which tools to use
            tool_plan = await self.plan_tools(message, intent, contexts)
            
            # Execute tools in parallel
            tool_results = await asyncio.gather(*[
                self.execute_tool(call) 
                for call in tool_plan[:5]  # Max 5 per round
            ])
            
            # Filter and accumulate
            filtered = await self.filter_results(tool_results, message)
            contexts.extend(filtered)
            
            # Check if sufficient
            if len(contexts) >= 10 or await self.is_sufficient(contexts, message):
                break
        
        # Stage 3: Generate response
        return await self.generate_response(message, contexts)
```

**Available Retrieval Tools**:

```python
LIFELOG_TOOLS = [
    # Vector-based context retrieval (like MineContext)
    "retrieve_activities",            # Search activity_context
    "retrieve_social_interactions",   # Search social_context  
    "retrieve_food_memories",         # Search food_context
    "retrieve_location_memories",     # Search location_context
    
    # Entity-based retrieval
    "retrieve_person_memories",       # Find memories with specific person
    "retrieve_place_memories",        # Find memories at specific place
    
    # Time-based retrieval
    "retrieve_daily_summaries",       # Get high-level day summaries
    "retrieve_time_range",            # Broad time filter
    
    # Aggregation tools (NEW for Lifelog)
    "aggregate_activity_duration",    # "How many hours did I spend reading?"
    "aggregate_activity_frequency",   # "How often did I exercise last month?"
    "aggregate_social_interactions",  # "Who did I see most last week?"
]
```

**Tool Planning Prompt**:

```yaml
lifelog_tool_planning: |
  User query: "{query}"
  Intent: {intent}
  
  Available tools:
  - retrieve_activities(query, categories, time_range, top_k)
  - retrieve_person_memories(person_name, query, time_range)
  - retrieve_food_memories(query, time_range)
  - retrieve_daily_summaries(date_range)
  - aggregate_activity_duration(category, time_range)
  - ... (12 total)
  
  Previously retrieved: {num_contexts} contexts in round {round}
  
  Plan 3-5 tool calls to answer this query. Consider:
  1. Time references (yesterday, last week, etc.)
  2. Entity mentions (people, places)
  3. Activity types (cooking, exercising)
  4. Need for statistics vs specific memories
  
  Example:
  Query: "What did I eat with Alice last week?"
  Plan:
  [
    {
      "tool": "retrieve_person_memories",
      "args": {
        "person_name": "Alice",
        "time_range": {"start": "2025-11-25", "end": "2025-12-01"}
      }
    },
    {
      "tool": "retrieve_food_memories",
      "args": {
        "query": "meals with Alice",
        "time_range": {"start": "2025-11-25", "end": "2025-12-01"}
      }
    },
    {
      "tool": "retrieve_social_interactions",
      "args": {
        "query": "Alice dining",
        "time_range": {"start": "2025-11-25", "end": "2025-12-01"}
      }
    }
  ]
  
  Return JSON array of tool calls.
```

#### Response Generation with Source Citations

```python
async def generate_response(self, query: str, contexts: List[ProcessedContext]) -> Dict:
    """
    Generate conversational response with source citations.
    """
    
    # Format contexts for prompt
    context_text = "\n\n".join([
        f"[{i+1}] {ctx.event_time.strftime('%b %d, %I:%M%p')} - {ctx.title}\n"
        f"   {ctx.summary}\n"
        f"   Activity: {ctx.activity_category}\n"
        f"   Entities: {', '.join([e['name'] for e in ctx.entities])}\n"
        f"   (source: {ctx.source_item_id})"
        for i, ctx in enumerate(contexts)
    ])
    
    prompt = f"""
    You are the user's personal memory assistant.
    Answer based ONLY on the retrieved contexts.
    
    Retrieved Contexts:
    {context_text}
    
    User Question: {query}
    
    Guidelines:
    - Use warm, conversational tone
    - Reference specific memories with dates/times
    - Cite sources using [1], [2] notation
    - For stats questions, calculate from context durations
    - If insufficient info, say so clearly
    
    Generate:
    1. answer: Main response text (with [N] citations)
    2. cited_context_ids: Array of context IDs cited
    """
    
    response = await self.llm.chat([{
        "role": "user",
        "content": prompt
    }], response_format="json")
    
    # Format for frontend
    return {
        "message": response["answer"],
        "sources": [
            {
                "context_id": ctx.id,
                "source_item_id": ctx.source_item_id,
                "thumbnail": get_thumbnail_url(ctx.source_item_id),
                "title": ctx.title,
                "timestamp": ctx.event_time,
                "snippet": ctx.summary[:150]
            }
            for ctx in contexts
            if ctx.id in response["cited_context_ids"]
        ]
    }
```

### 4.4 Daily Summary Generation (UPDATED)

**Inspired by MineContext's daily report generation**:

```python
@celery.task
async def generate_daily_summary(user_id: str, date: datetime.date):
    """
    Generate rich daily summary from all contexts.
    Runs nightly at 2am for previous day.
    """
    
    # 1. Gather all contexts from date
    contexts = await db.query(processed_content).filter(
        user_id=user_id,
        func.date(event_time) == date
    ).all()
    
    if not contexts:
        return
    
    # 2. Group by context type
    by_type = {
        "activities": [c for c in contexts if c.context_type == "activity_context"],
        "social": [c for c in contexts if c.context_type == "social_context"],
        "food": [c for c in contexts if c.context_type == "food_context"],
        "locations": [c for c in contexts if c.context_type == "location_context"]
    }
    
    # 3. Get top entities
    entity_counts = Counter()
    for ctx in contexts:
        for entity in ctx.entities:
            entity_counts[entity["name"]] += 1
    top_entities = entity_counts.most_common(10)
    
    # 4. Generate summary with LLM
    prompt = f"""
    Generate a warm, narrative daily summary for {date.strftime("%B %d, %Y")}.
    
    Activities ({len(by_type['activities'])}):
    {json.dumps([ctx.dict() for ctx in by_type['activities'][:20]], default=str, indent=2)}
    
    Social interactions ({len(by_type['social'])}):
    {json.dumps([ctx.dict() for ctx in by_type['social']], default=str, indent=2)}
    
    Meals ({len(by_type['food'])}):
    {json.dumps([ctx.dict() for ctx in by_type['food']], default=str, indent=2)}
    
    Top people/places: {', '.join([name for name, _ in top_entities])}
    
    Write a 3-paragraph summary in markdown:
    1. **Morning & Afternoon**: Main activities and interactions
    2. **Evening**: What happened later in the day
    3. **Highlights**: Most memorable moments
    
    Use friendly, past tense. Reference specific times and people.
    Include [event:ID] links for key moments.
    """
    
    summary_markdown = await llm.chat([{
        "role": "user",
        "content": prompt
    }])
    
    # 5. Store in database
    await db.insert(daily_summaries, {
        "user_id": user_id,
        "summary_date": date,
        "content_markdown": summary_markdown,
        "source_event_ids": [ctx.id for ctx in contexts[:50]]  # Track sources
    })
    
    # 6. Generate embedding for the summary (for retrieval)
    summary_embedding = await embedding.embed(summary_markdown)
    
    # 7. Store summary in Qdrant too
    await qdrant.upsert(
        collection_name=f"user_{user_id}",
        points=[{
            "id": f"daily_summary_{date.isoformat()}",
            "vector": summary_embedding,
            "payload": {
                "context_type": "daily_summary",
                "date": date.isoformat(),
                "title": f"Daily Summary: {date.strftime('%b %d, %Y')}",
                "summary": summary_markdown,
                "importance": 0.9  # High importance for daily summaries
            }
        }]
    )
```

**Example Generated Summary**:

```markdown
# November 2, 2025

## Morning & Afternoon
Your day started with a peaceful morning jog through Central Park around 7am, followed by a healthy breakfast at home. You spent most of the morning working from your home office, with a quick coffee break at Blue Bottle around 11am where you ran into Sarah. The afternoon was productive - you wrapped up the presentation slides and had a video call with the team at 2pm.

## Evening
Around 6pm, you started cooking dinner, making your signature pasta carbonara. Alice and Bob came over around 7pm, and you all enjoyed the meal together on the patio. The conversation was lively - you discussed summer travel plans and Bob's new startup idea. Everyone stayed until about 10pm.

## Highlights
The standout moment was definitely [event:abc123] the dinner party - the combination of good food, great friends, and warm weather made it really special. Also notable was [event:def456] finally finishing that presentation you'd been working on all week. You seemed energized and accomplished throughout the day.

**Stats**: 2.5 hours socializing • 5 hours working • 1 hour exercising
**People**: Alice, Bob, Sarah
**Places**: Home, Central Park, Blue Bottle Cafe
```

---

## Section 5: Updated Implementation Prompts

### 5.1 Image Analysis Prompt

```yaml
lifelog_image_analysis_v1: |
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
  
  ## Output Format:
  
  Return JSON:
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
        },
        {
          "context_type": "location_context",
          "title": "Central Park",
          "summary": "Running path near the reservoir, trees in autumn colors",
          "entities": [
            {"type": "place", "name": "Central Park Reservoir", "confidence": 0.8}
          ],
          "importance": 0.5,
          "confidence": 0.85
        }
      ]
    },
    "image_1": { ... }
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
```

### 5.2 Semantic Merging Prompt

```yaml
lifelog_semantic_merging_v1: |
  You are merging multiple memory contexts from a 2-hour time window.
  
  Input: {num_contexts} contexts of type "{context_type}"
  Time range: {start_time} to {end_time}
  
  Contexts:
  {contexts_json}
  
  ## Task:
  Decide which contexts represent the SAME continuous activity (merge) vs DISTINCT activities (keep separate).
  
  ## Merge Decision Criteria:
  
  ### MERGE if:
  - Same activity category and logically continuous
  - Same location and participants
  - Time gaps < 30 minutes
  - Example: 3 "cooking" contexts from 6:00-7:00pm → merge into "Cooking dinner"
  
  ### KEEP SEPARATE if:
  - Different activity categories
  - Different locations
  - Different participants
  - Meaningful activity transitions
  - Example: "cooking" at 6pm + "eating" at 7pm → keep as 2 events
  
  ## For Merged Contexts:
  - title: Create encompassing title
  - summary: Combine details from all merged contexts
  - entities: Merge and deduplicate entity lists
  - duration_minutes: Calculate from time span
  - importance: Take MAX importance score
  - confidence: AVERAGE confidence scores
  - keywords: Merge and deduplicate
  
  ## Output Format:
  
  Return JSON:
  {
    "decisions": [
      {
        "merge_type": "merged",
        "merged_indices": [0, 1, 2],
        "data": {
          "title": "Cooking Italian dinner",
          "summary": "Prepared pasta carbonara from scratch, making the sauce with eggs and bacon, boiling fresh pasta, and plating beautifully. Took time to get everything just right.",
          "activity_category": "cooking",
          "entities": [
            {"type": "place", "name": "home kitchen", "confidence": 0.9},
            {"type": "object", "name": "pasta maker", "confidence": 0.85},
            {"type": "food", "name": "carbonara ingredients", "confidence": 0.9}
          ],
          "keywords": ["cooking", "Italian", "pasta", "carbonara", "homemade"],
          "duration_minutes": 75,
          "importance": 0.75,
          "confidence": 0.88
        }
      },
      {
        "merge_type": "keep_separate",
        "indices": [3, 4]
      }
    ]
  }
  
  ## Important:
  - Be conservative - only merge if clearly the same activity
  - Preserve important transitions (cooking → eating)
  - Create rich merged summaries with details from all sources
  - Maintain temporal coherence
```

---

## Section 6: Success Metrics (UPDATED)

### Ingestion Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Context extraction accuracy** | 90%+ contexts correctly classified | Manual review of 100 sample images |
| **Entity recognition precision** | 85%+ entities correctly identified | Human validation on test set |
| **Multi-context coverage** | Avg 2.5 contexts per image | Count contexts/image in processed_content |
| **Merge accuracy** | 80%+ merge decisions correct | Review merged events vs ground truth |

### RAG Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Retrieval precision @10** | 80%+ relevant contexts in top 10 | Eval set with labeled queries |
| **Tool selection accuracy** | 75%+ correct tool plans | Manual review of agent tool calls |
| **Response factual accuracy** | 90%+ responses grounded in contexts | Verify citations match sources |
| **Daily summary quality** | 4/5 average rating | User thumbs up/down feedback |

### Performance Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Batch processing throughput** | 10 images/minute | Processing pipeline metrics |
| **Chat response latency** | <3s p95 | API latency histogram |
| **Context retrieval latency** | <500ms per tool | Qdrant query time |
| **Daily summary generation time** | <2 minutes per day | Celery task duration |
