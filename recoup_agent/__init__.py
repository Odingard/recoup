def __getattr__(name):
    # Lazy so importing pure modules (reconciliation, rights_discovery.compiler,
    # rights_discovery.runtime) never pulls in google.adk.
    if name == "agent":
        import importlib
        return importlib.import_module(".agent", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
