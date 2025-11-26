import os
import operator
from typing import TypedDict, Annotated, Sequence

import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

async def main():
    # MCP tool server setup
    servers = {
        "math": {
            "transport": "stdio",
            "command": "python",
            "args": ["mcp_unified_server.py"],
        }
    }
    client = MultiServerMCPClient(servers)
    tools = await client.get_tools()  # List of tool objects

    llm = ChatOllama(model="llama3.2")   # Change as needed for your Ollama
    model_with_tools = llm.bind_tools(tools)

    # Define agent graph state
    class AgentState(TypedDict):
        messages: Annotated[Sequence[BaseMessage], operator.add]
    
    def call_model(state: AgentState):
        messages = state["messages"]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    tool_node = ToolNode(tools)

    def should_continue(state: AgentState):
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "continue"
        return "end"

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", tool_node)
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent", should_continue,
        {"continue": "tools", "end": END}
    )
    workflow.add_edge("tools", "agent")
    graph = workflow.compile()

    messages = []
    print("Ollama React Agent (LangGraph/MCP). Type 'quit' to exit.")
    while True:
        user_input = input("You: ")
        if user_input.strip().lower() in ("quit", "exit"):
            break
        messages.append(HumanMessage(content=user_input))
        result = await graph.ainvoke({"messages": messages})
        new_messages = result["messages"]

        # Tool call notification
        tool_calls = []
        for msg in new_messages[::-1]:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = msg.tool_calls
                break
        if tool_calls:
            for call in tool_calls:
                print(f">>> [TOOL CALL] Agent is using tool '{call['name']}' with arguments {call['args']}")

        messages = new_messages
        print("AI:", messages[-1].content)

if __name__ == "__main__":
    asyncio.run(main())
