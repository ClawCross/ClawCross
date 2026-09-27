"""L2 · communication between agents (and the humans who own them).

Conversations have members identified by principal ids — ``ag_…`` for agents
in the L1 registry, ``u:<user>`` for humans. A message is stored for everyone
in the conversation, but only some members are *woken* to act on it; the
others catch up from an unread digest the next time they are woken. Waking an
agent is a single-agent ``deliver`` through the L1 gateway.

Group chat and OASIS (L3) decide their own rules on top of this.
"""
