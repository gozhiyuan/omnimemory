---
name: transcription
version: "1.0.0"
description: Verbatim transcription of audio/video
output_format: text
required_vars:
  - media_kind
  - language_guidance
---

You are transcribing personal lifelog {{media_kind}}.

Return the verbatim transcript as plain text. Do not add commentary, speaker labels, or timestamps.

{{language_guidance}}
