"""Adapter package for the contribution center.

Adapters wrap third-party systems (mini-swe-agent, badge services, etc.)
behind narrow interfaces. They never talk to the network directly; the
network is the responsibility of the bot that calls them.
"""
