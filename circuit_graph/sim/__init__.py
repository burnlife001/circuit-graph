"""SPICE simulation pipeline for circuit-graph.

Phase 1: hardcoded LM321 proof-of-concept.
Phase 3+: reads v2 primitive ``simulation`` fields for template-driven assembly.
"""

from circuit_graph.sim.spice_deck import SimulationDeck, ProbeDefinition
from circuit_graph.sim.spice_result import AnalysisResult, SpiceResult
from circuit_graph.sim.spice_runner import SpiceRunner, SpiceRunnerConfig
from circuit_graph.sim.deck_builder import DeckBuilder
from circuit_graph.sim.deck_validator import DeckValidator
from circuit_graph.sim.stimulus import StimulusSelector
from circuit_graph.sim.convergence import ConvergenceConfig

__all__ = [
    "AnalysisResult",
    "ConvergenceConfig",
    "DeckBuilder",
    "DeckValidator",
    "ProbeDefinition",
    "SimulationDeck",
    "SpiceResult",
    "SpiceRunner",
    "SpiceRunnerConfig",
    "StimulusSelector",
]
