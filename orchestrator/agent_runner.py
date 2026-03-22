"""Runs a single bot through its agentic tool-use loop (multi-tenant)."""

import json
import os
import anthropic
from dotenv import load_dotenv
from api.usage_log import log_usage

load_dotenv()
from orchestrator.tools import TOOL_DEFINITIONS, TOOL_HANDLERS


def run_agent(org_id: str, agent_config: dict, system_prompt: str, date: str) -> dict:
    """
    Run one agent. Sends system prompt + tools to the API,
    loops on tool calls until the bot stops or hits max_iterations.
    Returns the bot's final structured output.
    """
    client = anthropic.Anthropic()

    tools = [TOOL_DEFINITIONS[t] for t in agent_config.get("tools", [])]
    max_iter = agent_config.get("max_iterations", 10)

    messages = [
        {"role": "user", "content": "Begin your work for today's cycle. Date: " + date},
    ]

    final_text = ""
    iterations = 0
    total_input = 0
    total_output = 0
    api_calls = 0

    while iterations < max_iter:
        iterations += 1

        kwargs = {
            "model": agent_config["model"],
            "max_tokens": agent_config.get("max_tokens", 4096),
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = client.messages.create(**kwargs)
        total_input += getattr(response.usage, "input_tokens", 0)
        total_output += getattr(response.usage, "output_tokens", 0)
        api_calls += 1

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        if text_parts:
            final_text += ("\n" if final_text else "") + "\n".join(text_parts)

        if not tool_calls or response.stop_reason == "end_turn":
            break

        assistant_content = response.content
        tool_results = []

        for tc in tool_calls:
            handler = TOOL_HANDLERS.get(tc.name)
            if handler:
                if tc.name == "save_evidence":
                    result = handler(tc.input, org_id, agent_config["id"], date)
                else:
                    result = handler(tc.input)
            else:
                result = f"Unknown tool: {tc.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": str(result),
            })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    # Log usage
    from api.usage import PRICING
    model = agent_config["model"]
    prices = PRICING.get(model, PRICING["claude-haiku-4-5-20251001"])
    cost = round(
        (total_input / 1_000_000) * prices["input"]
        + (total_output / 1_000_000) * prices["output"],
        4,
    )
    log_usage(
        org_id,
        agent_config["id"],
        {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "api_calls": api_calls,
            "cost_usd": cost,
            "model": model,
        },
        cycle_date=date,
    )

    output = {"raw_text": final_text, "agent_id": agent_config["id"]}
    try:
        if "```json" in final_text:
            json_str = final_text.split("```json")[1].split("```")[0].strip()
            output["structured"] = json.loads(json_str)
        elif final_text.strip().startswith("{"):
            output["structured"] = json.loads(final_text.strip())
    except (json.JSONDecodeError, IndexError):
        pass

    return output
