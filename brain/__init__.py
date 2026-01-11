"""
Brain module - MCP server and Agent-S orchestration
"""

from brain.agent_manager import (
    get_agent_instance,
    initialize_agent,
    shutdown_agent,
    is_agent_ready,
    restart_agent,
    AgentExecutionError,
)

__all__ = [
    "get_agent_instance",
    "initialize_agent",
    "shutdown_agent",
    "is_agent_ready",
    "restart_agent",
    "AgentExecutionError",
]
