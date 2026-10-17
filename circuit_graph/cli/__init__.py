"""CLI package — backward-compatible facade.

Historically everything lived in ``circuit_graph/cli.py``.  The package
keeps the old import paths working (``from circuit_graph.cli import main``,
``_parse_and_enrich``, ``layout_check``, ...) while commands are split into
focused modules.  The console entry point ``cg = circuit_graph.cli:main``
is unchanged.
"""

from circuit_graph.cli.app import (
    _BUNDLED_SKILLS_DIR,
    _discover_bundled_docs,
    _discover_bundled_skills,
    _parse_and_enrich,
    design,
    erc,
    export,
    init_cmd,
    init_orchestrator_cmd,
    layout_check,
    main,
    sim,
    view,
)
from circuit_graph.cli.kb import kb

__all__ = [
    "_BUNDLED_SKILLS_DIR",
    "_discover_bundled_docs",
    "_discover_bundled_skills",
    "_parse_and_enrich",
    "design",
    "erc",
    "export",
    "init_cmd",
    "init_orchestrator_cmd",
    "kb",
    "layout_check",
    "main",
    "sim",
    "view",
]
