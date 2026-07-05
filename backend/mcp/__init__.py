"""MCP execution layer (Phase C).

HIVE manages MCP at the SERVER level: the catalog decides WHICH servers
each agent gets; the claude CLI's own MCP client handles tool loading
within those servers. Deliberately no tool-level budgeting/proxy layer.
"""
