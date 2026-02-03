---
name: agent_action
version: "1.0.0"
description: Determine agent action from user message
output_format: json
required_vars:
  - conversation_history
  - message
---

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
