# Invariants

Statements of desired state. Each unchecked item is verified on every
`mcloop maintain` run. Check an item to retire it.

- [ ] Every .py file in mcloop/ has a corresponding entry in the Core Modules section of CLAUDE.md
- [ ] The reviewer model in .mcloop/config.json is the most capable current DeepSeek model on OpenRouter. "Most capable" means: highest version number in the deepseek/ namespace (e.g. deepseek-v4 > deepseek-v3), breaking ties by the highest point release (e.g. v3.2 > v3.1), breaking further ties by most recent release date. Use WebFetch on https://openrouter.ai/api/v1/models to get the current catalog and filter for model IDs starting with "deepseek/deepseek". If the best choice is ambiguous (multiple new models with genuine tradeoffs, not just size variants), ask the user via Telegram before deciding. Update .mcloop/config.json if a newer model is available.
