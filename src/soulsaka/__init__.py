"""soulsaka: a local continual-personalization pipeline.

Everything in this package runs on your own machines. The hub (``soulsaka serve``)
owns the corpus, transcribes speech, verifies who is talking, extracts memories,
serves the current adapter and runs retrains and evals. Thin clients (the web app,
the always-on listener, the importers) capture data and push it to the hub.
"""

__version__ = "0.1.0"
