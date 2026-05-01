# Agent Logic
from typing import Optional

from state import AgentState
from llm import agent_step
from tools import execute_tool, get_allowed_tool_names


def run_agent_tick(state: AgentState) -> tuple[str, bool]:
    """
    Execute one agent step.
    
    Args:
        state: Current agent state
    
    Returns:
        (message_to_user, is_done)
    """
    # Consume pending notes
    notes = state.consume_notes()
    
    # Get previous tool result if any
    tool_result = None
    if state.history and state.history[-1].get("role") == "tool_result":
        tool_result = state.history[-1].get("content")
    
    # Get agent decision
    decision = agent_step(
        goal=state.goal,
        history=[h for h in state.history if h.get("role") != "tool_result"],
        pending_notes=notes,
        tool_result=tool_result
    )
    
    # Handle error
    if decision.get("action") == "error":
        error_msg = decision.get("message", "Unknown error")
        raw = decision.get("raw", "")
        state.add_error(f"JSON parse failed: {error_msg}\nRaw: {raw[:200]}")
        state.save()
        return f"❌ Agent error: {error_msg}", False
    
    # Record decision in history
    state.history.append({
        "role": "assistant",
        "content": str(decision)
    })
    
    action = decision.get("action", "")
    message = decision.get("message", "")
    note = decision.get("note", "")
    
    if note:
        state.next = note
    
    # Handle finish
    if action == "finish":
        state.mark_done()
        return f"✅ Task complete: {message}", True
    
    # Handle say
    if action == "say":
        state.increment_step()
        state.save()
        return f"💬 {message}", False
    
    allowed_tools = get_allowed_tool_names(
        allow_tools=True,
        is_owner=False,
        is_dm=False,
        has_allowed_role=False,
        privileged_confirmed=False,
    )
    tool_name = decision.get("tool_name", "")
    
    # If action == "tool" use tool_name, else check if action is a tool name
    if action == "tool":
        pass  # use tool_name from decision
    elif action in allowed_tools:
        # Fallback: action contains the tool name
        tool_name = action
    else:
        # Unknown action
        state.add_error(f"Unknown action: {action}")
        state.save()
        return f"❌ Unknown action: {action}", False
    
    args = decision.get("args", {})

    if tool_name not in allowed_tools:
        state.add_error(f"Tool not allowed: {tool_name}")
        state.save()
        return f"❌ Tool not allowed: {tool_name}", False
    
    # Execute tool
    result = execute_tool(tool_name, args, allowed_tool_names=allowed_tools)
    
    # Record result
    state.history.append({
        "role": "tool_result",
        "content": result
    })
    
    state.increment_step()
    state.save()
    
    # Format output
    result_preview = result[:200] + "..." if len(result) > 200 else result
    return f"🔧 {tool_name}({args}) → {result_preview}", False
