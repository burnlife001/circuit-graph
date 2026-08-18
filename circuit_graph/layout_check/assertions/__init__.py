"""Built-in layout assertions, one class per kind.

The 12 built-in kinds (see :data:`circuit_graph.layout_check.model.BUILT_IN_ASSERTION_KINDS`)
each live in a sibling file in this package.

``base.py`` defines :class:`Assertion` ABC + spatial helpers. Each of ``flow.py``,
``power.py``, ``ground.py``, ``alignment.py``, ``decoupling.py``, ``annotation.py``,
``routing.py`` defines a class implementing one assertion.

Boundary semantics are uniform: ``distance <= tolerance_mm`` passes; ``> tolerance_mm`` fails.
"""
