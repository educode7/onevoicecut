"""Controllers: the half of presentation that talks to the application layer.

Kept apart from the routes because the two answer different questions. A route
is HTTP — path, headers, status codes; a controller is the translation between
that and a command. Splitting them is what lets the controller be tested
without a client, and the routes be moved without touching a decision.
"""
