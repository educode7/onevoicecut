"""Presentation-layer tests for the jobs module.

These sit with the module rather than under the web adapter because what they
prove is a property of the module's HTTP surface — precedence, ownership
translation, streaming — and not of the adapter that used to hold it. Keeping
them beside `presentation/` means a rule change in `tests/test_architecture.py`
and the tests that would catch it move in the same commit.
"""
