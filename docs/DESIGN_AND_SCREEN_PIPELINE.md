# Design and Screen Pipeline

Figma is editable source of truth for frames, components, variables and prototypes. GitHub stores PRD, flows, screen inventory, token notes, Figma links, review previews and code. PNGs are not editable source. Do not commit proprietary .fig files.

ChatGPT: research synthesis, UX flow, screen inventory, design requirements and Figma creation/review where connected. Claude: read approved Figma context through MCP, implement UI/states and submit matching-viewport screenshots/preview. Human: approve direction and material changes.

Flow: PRD → primary journey → screen/state inventory → tokens/components → editable Figma → record URL/page/frame IDs/version in docs/design/README.md → Claude implementation → visual/functional comparison.

App: home, core action, result/confirmation and essential onboarding/settings only. Game: title/start, play loop, pause/resume, result, replay. Define loading/empty/error/success/disabled/validation, responsive behaviour and accessibility.
