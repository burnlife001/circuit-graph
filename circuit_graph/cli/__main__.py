"""Make the CLI runnable as a module: ``python -m circuit_graph.cli <cmd>``."""

from circuit_graph.cli.app import main

if __name__ == "__main__":
    main()
