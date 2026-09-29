"""Deliberately empty since slice 4e — and deliberately still present.

The HTTP surface moved out in two steps: the five jobs operations into
`systems/pipeline/jobs/presentation/` (2e), then the three clip operations into
`systems/pipeline/clips/presentation/` (4e), which drained the last content from
here. The injectable composition object those routers were built from,
`WebDependencies`, moved with them into `main.py` — beside the two factories
that construct adapters, because only a composition root may do that.

What is left is this package so any remaining reference still resolves. Slice
5c.4 deletes it; until then, empty is the point, not an oversight.
"""
