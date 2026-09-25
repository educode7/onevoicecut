"""The jobs module's HTTP surface.

Kept apart from the ports and the application layer on purpose: this package is
the only place in the module that knows what a browser is allowed to send and
what it gets back. `adapters/web/` composes it, it does not contain it.
"""
