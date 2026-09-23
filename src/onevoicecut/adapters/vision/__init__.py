"""The vision adapter package: where the preacher is, from real frames.

Two modules, split the way `adapters/asr/local` split its probe from its call:
`declarations` answers what this install can do without importing anything
heavy, and `torchvision_tracker_adapter` makes the detection call itself.
"""
