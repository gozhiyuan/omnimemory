---
name: agent_response
version: "1.0.0"
description: Generate agent response after action execution
output_format: text
required_vars:
  - conversation_history
  - action_result
  - session_state
---

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
