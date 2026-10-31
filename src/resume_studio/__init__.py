"""Resume Studio: offline resume tailoring (JSON base + patch -> LaTeX -> PDF).

No LLM calls and no API keys. A tailoring patch is produced elsewhere (Claude chat)
and pasted into the dashboard; this package validates it, renders LaTeX, compiles
a PDF and keeps a history of builds.
"""
