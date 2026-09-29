"""L3 · group chat: WeChat-style conversations between a person and their agents.

``conversations`` / ``delivery`` / ``store`` hold the conversations, their members
(``ag_…`` agents, ``u:<user>`` humans) and messages, and wake the members a
message is for through the L1 gateway; the others catch up from an unread digest
the next time they are woken. ``service`` / ``routes`` are the rules on top.
"""
