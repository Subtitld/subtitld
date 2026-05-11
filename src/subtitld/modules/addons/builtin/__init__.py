"""Built-in providers shipped inside the Subtitld binary.

These do NOT go through `AddonProcess` — they're regular in-process Python
modules that implement the same `Provider` ABCs as out-of-process add-ons,
so the rest of the app can talk to them through one uniform interface.
"""
