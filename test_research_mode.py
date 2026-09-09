# -*- coding: utf-8 -*-
"""Research mode: the tool-call loop, and what happens when it runs out.

    python test_research_mode.py

Research mode used to fail outright. The loop allowed four tool-call rounds,
which is plenty for "is it safe today" and nowhere near enough for a research
question that has to find a dataset, read its metadata, pull a series and
compare it. On the fifth round the agent raised, the API answered 503, and the
console printed "the grounded marine agent is unavailable".

Groq is mocked here on purpose. These are tests of the LOOP - how many rounds
it allows, what it does when they run out, and whether it survives a tool name
the model invented - and a live model would make them non-deterministic
without testing anything extra. The live path is test_call_agent.py and
test_sources.py.
"""

from __future__ import annotations

import json
import sys

from erddap_client import ERDDAPClient
from groq_agent import ERDDAPTools, GroqAgent, GroqError

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if detail and not condition else ""))


def tool_call(name: str, arguments: dict | None = None) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": f"c-{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {})},
        }],
    }


def agent(replies: list[dict], **kwargs) -> tuple[GroqAgent, list]:
    """An agent whose model returns `replies` in order. Records every request."""
    seen: list = []
    instance = GroqAgent(
        ERDDAPTools(ERDDAPClient(timeout=1, verify_ssl=False), **kwargs),
        model="test", base_url="http://localhost", api_key="test-key",
    )

    def fake_chat(messages, include_tools=True, mode="normal"):
        seen.append({"messages": list(messages), "include_tools": include_tools})
        if not replies:
            raise AssertionError("the model was called more times than the test scripted")
        return {"message": replies.pop(0)}

    instance._chat = fake_chat  # noqa: SLF001 - the point of the test
    return instance, seen


def stub_tools(instance: GroqAgent, payload: dict) -> None:
    """Every tool returns `payload`, so the loop is what is under test."""
    instance.tools.execute = lambda name, arguments: dict(payload)


def main() -> int:
    print("\n1. Research mode gets more rounds than normal mode")
    # Nine tool rounds then an answer: impossible under the old budget of four.
    replies = [tool_call("get_current_conditions") for _ in range(9)]
    replies.append({"role": "assistant", "content": "Sea surface temperature is 29.4 degC."})
    instance, seen = agent(list(replies))
    stub_tools(instance, {"parameters": {"seaSurfaceTemperature": {"value": 29.4}}})
    result = instance.answer("compare SST across the shelf", mode="research", trace=False)
    check("ten rounds completed", len(seen) == 10, f"{len(seen)} calls")
    check("answered instead of raising", "29.4" in result["response"])
    check("every tool call recorded", len(result["tool_calls"]) == 9, str(len(result["tool_calls"])))
    check("not marked truncated", not result.get("truncated"))

    print("\n2. Normal mode still stops at four")
    replies = [tool_call("get_current_conditions") for _ in range(4)]
    replies.append({"role": "assistant", "content": "Waves are about one metre."})
    instance, seen = agent(list(replies))
    stub_tools(instance, {"parameters": {"waveHeight": {"value": 1.0}}})
    result = instance.answer("how are the waves", mode="normal", trace=False)
    # Four tool rounds, then the withdrawn-tools finish: five model calls.
    check("stopped after four tool rounds", len(seen) == 5, f"{len(seen)} calls")
    check("last call had tools withdrawn", seen[-1]["include_tools"] is False)
    check("marked truncated", result.get("truncated") is True)

    print("\n3. Running out of rounds answers from what was gathered")
    replies = [tool_call("get_current_conditions") for _ in range(10)]
    replies.append({"role": "assistant", "content": "From the data gathered: SST 29.4 degC."})
    instance, seen = agent(list(replies))
    stub_tools(instance, {"parameters": {"seaSurfaceTemperature": {"value": 29.4}}})
    try:
        result = instance.answer("exhaust the budget", mode="research", trace=False)
        raised = False
    except GroqError as exc:
        result, raised = {}, True
        print(f"        raised: {exc}")
    check("did not raise", not raised)
    check("answered from gathered data", "29.4" in result.get("response", ""))
    check("flagged as partial", result.get("truncated") is True)
    check("the ten tool results are kept", len(result.get("returned_data", [])) == 10,
          str(len(result.get("returned_data", []))))

    print("\n4. A tool name the model invented does not kill the request")
    instance, seen = agent([
        tool_call("get_sea_monsters"),
        {"role": "assistant", "content": "There is no such dataset."},
    ])
    try:
        result = instance.answer("find sea monsters", mode="research", trace=False)
        raised = False
    except GroqError:
        result, raised = {}, True
    check("did not raise", not raised)
    check("the model was told what went wrong",
          "no tool called" in json.dumps(result.get("returned_data", [])))
    check("the invented tool is not cited",
          not any(item.get("usable") for item in result.get("returned_data", [])))

    print("\n5. Bad arguments are reported, not fatal")
    instance, _ = agent([
        tool_call("get_tides", {"nonsense": True}),
        {"role": "assistant", "content": "I could not read the tide."},
    ])
    try:
        result = instance.answer("tides please", mode="research", trace=False)
        raised = False
    except (GroqError, TypeError):
        result, raised = {}, True
    check("did not raise", not raised)
    check("argument error surfaced to the model",
          "does not accept" in json.dumps(result.get("returned_data", [])))

    print("\n6. Sources are openable pages, and only for tools that returned data")
    instance, _ = agent([
        tool_call("get_nearest_fishing_zones"),
        {"role": "assistant", "content": "Three advisories nearby."},
    ])
    instance.tools.execute = lambda name, arguments: {
        "zones": [{"distanceKm": 13.8}], "status": "AVAILABLE",
    }
    result = instance.answer("nearest PFZ", mode="research", trace=False)
    references = result.get("references", [])
    check("a reference was produced", len(references) == 1, str(references))
    check("it carries a real URL", references and references[0]["url"].startswith("https://"))
    check("it names the product",
          references and "Potential Fishing Zone" in references[0]["title"])

    instance, _ = agent([
        tool_call("get_marine_alerts"),
        {"role": "assistant", "content": "No advisory is in force."},
    ])
    instance.tools.execute = lambda name, arguments: {"alerts": [], "status": "NOT AVAILABLE"}
    result = instance.answer("any warnings", mode="research", trace=False)
    check("an empty tool is not cited as a source", result.get("references") == [],
          str(result.get("references")))

    print("\n7. An ERDDAP dataset links to its own page")
    instance, _ = agent([
        tool_call("get_time_series", {"dataset_id": "incois_argo_mnt_VAM", "variable": "TEMP",
                                      "bbox": [8, 20, 75, 90], "start": "2026-01-01", "end": "2026-06-01"}),
        {"role": "assistant", "content": "| Date | Temp |\n| --- | ---: |\n| 2026-01 | 28.1 |"},
    ])
    instance.tools.execute = lambda name, arguments: {"records": [{"time": "2026-01", "TEMP": 28.1}]}
    result = instance.answer("argo temperature series", mode="research", trace=False)
    urls = [reference["url"] for reference in result.get("references", [])]
    check("links to the dataset, not the catalogue",
          any("info/incois_argo_mnt_VAM/index.html" in url for url in urls), str(urls))

    print("\n8. The research voice asks for what the console can now render")
    instance, seen = agent([{"role": "assistant", "content": "Answer."}])
    instance.answer("anything", mode="research", trace=False)
    system = seen[0]["messages"][0]["content"]
    for phrase, why in [
        ("MARKDOWN TABLE", "tables now render, so the model should use them"),
        ("label or timestamp FIRST", "that column order is what the chart reads"),
        ("units in the header", "the chart takes its series names from there"),
        ("LIMITS", "a research answer states what it cannot settle"),
    ]:
        check(f"voice asks for: {phrase}", phrase in system, why)

    print("\n9. The voice mode is not the research voice")
    instance, seen = agent([{"role": "assistant", "content": "Answer."}])
    instance.answer("anything", mode="voice", trace=False)
    system = seen[0]["messages"][0]["content"]
    check("no markdown on a phone call", "No markdown, lists, headings" in system)
    check("asks the caller for what it needs",
          "ask ONE short question in his language" in system)
    check("units spelled as words", "Never nm, km, m/s" in system)
    check("distances in kilometres", "KILOMETRES" in system)
    check("not the research voice", "MARKDOWN TABLE" not in system)

    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for name in FAIL:
            print(f"  failed: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
