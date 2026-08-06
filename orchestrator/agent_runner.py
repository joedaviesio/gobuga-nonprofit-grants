"""Runs a single bot through its agentic tool-use loop (multi-tenant)."""

import json
import os
import time
import anthropic
from dotenv import load_dotenv
from api.usage_log import log_usage

load_dotenv()
from orchestrator.tools import TOOL_DEFINITIONS, TOOL_HANDLERS


def _create_with_retry(client, kwargs, max_retries: int = 4):
    """Call Anthropic messages.create with exponential backoff on 429s.
    Honours `retry-after` header when present; otherwise backs off 30, 60, 120, 240s."""
    backoff_schedule = [30, 60, 120, 240]
    last_err = None
    for attempt in range(max_retries):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            last_err = e
            wait = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
            try:
                # Anthropic sometimes includes retry-after seconds
                ra = e.response.headers.get("retry-after") if getattr(e, "response", None) else None
                if ra:
                    wait = max(wait, int(float(ra)))
            except (AttributeError, ValueError, TypeError):
                pass
            print(f"  [429 rate-limit; sleeping {wait}s before retry {attempt + 2}/{max_retries}]", flush=True)
            time.sleep(wait)
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529):
                last_err = e
                wait = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                print(f"  [{e.status_code} from API; sleeping {wait}s before retry {attempt + 2}/{max_retries}]", flush=True)
                time.sleep(wait)
            else:
                raise
    raise last_err if last_err else RuntimeError("retry exhausted with no exception captured")


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
    save_evidence_calls = 0
    reminder_sent = False
    requires_evidence = "save_evidence" in agent_config.get("tools", [])

    # Allow up to 2 bonus iterations for the evidence reminder so it
    # doesn't get swallowed when the model burns through max_iter on searches.
    effective_max = max_iter
    while iterations < effective_max:
        iterations += 1

        kwargs = {
            "model": agent_config["model"],
            "max_tokens": agent_config.get("max_tokens", 4096),
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = _create_with_retry(client, kwargs)
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

        if not tool_calls:
            if requires_evidence and save_evidence_calls == 0 and not reminder_sent:
                reminder_sent = True
                effective_max = iterations + 2  # grant bonus iterations for evidence saving
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": (
                        "STOP. You have not called save_evidence yet. Every opportunity, "
                        "observation, or conclusion you mentioned above is INVISIBLE to the "
                        "next agent unless saved. Call save_evidence now for each finding. "
                        "If you genuinely found nothing, call save_evidence once with "
                        "type='analysis' and title='No opportunities found'."
                    ),
                })
                continue
            break

        # Process tool calls before checking stop_reason — ensures all
        # save_evidence calls are executed even on the model's final turn.
        assistant_content = response.content
        tool_results = []

        for tc in tool_calls:
            handler = TOOL_HANDLERS.get(tc.name)
            if handler:
                if tc.name == "save_evidence":
                    result = handler(tc.input, org_id, agent_config["id"], date)
                    save_evidence_calls += 1
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

        if response.stop_reason == "end_turn":
            if requires_evidence and save_evidence_calls == 0 and not reminder_sent:
                reminder_sent = True
                effective_max = iterations + 2  # grant bonus iterations for evidence saving
                messages.append({
                    "role": "user",
                    "content": (
                        "You ran searches but have not called save_evidence yet. "
                        "Call save_evidence now for every opportunity or finding. "
                        "If truly nothing was found, save one evidence item with "
                        "type='analysis' and title='No opportunities found'."
                    ),
                })
                continue
            break

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
