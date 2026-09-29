"""Request and response shapes, split from the routes that serve them.

A shape is a promise about bytes on the wire; a route is a promise about
behaviour. Tests pin the first without booting an app, and the second without
importing a pydantic model it does not care about.
"""
