# Cost Controls

Provider and cloud budgets must be set before unattended execution. ChatGPT/Claude subscription does not automatically include API credits for Actions.

Per run: wall-clock timeout, repair cap, max parallel jobs, token/API budget where supported, tool-call/deployment limits, stop when quota unknown.

Pilot hypotheses: one build/product; max 3 repairs; 30-min task timeout; USD 5/task and USD 25/experiment planning caps. Not provider-enforced; configure actual hard limits separately.

Report actual API/cloud spend, duration and tokens only when available. Stop on exhausted cap, unknown expensive action cost, repeated same failure or unattributed cloud spend.
