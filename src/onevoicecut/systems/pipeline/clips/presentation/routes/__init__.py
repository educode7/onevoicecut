"""Routes: the half of presentation that speaks HTTP.

Kept apart from the controllers because the two answer different questions.
A route knows about paths, headers, status codes and the principal gate; a
controller knows about commands. Splitting them is what lets the routes move
with a version prefix without touching a decision, and lets the generated 401
check keep deriving from a route table that declares `principal` on every
entry.
"""
