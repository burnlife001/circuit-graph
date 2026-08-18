"""Layout rule checker — declarative .schlayout YAML rules with binary pass/fail.

Companion package to ``circuit_graph.layout`` (the placement generator).
Adds geometry-aware rule validation as a CLI subcommand ``cg layout-check``.

Public surface (populated by later batches):
- :class:`~circuit_graph.layout_check.model.RuleSpec`
- :func:`~circuit_graph.layout_check.loader.load_rules`
- :class:`~circuit_graph.layout_check.checker.LayoutChecker`
"""
