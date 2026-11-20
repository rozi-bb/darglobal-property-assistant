"""Tools agent.

Belum ada tool. Rencana (docs/architecture.md, roadmap Milestone 3):
- `search_darglobal`: retrieval teks halaman DarGlobal
- `search_wasalt_listings`: filter iklan Wasalt terstruktur
- `wasalt_market_stats`: statistik harga per kota/tipe
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

TOOLS: list[BaseTool] = []
