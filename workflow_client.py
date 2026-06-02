import json
import os
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _get_data_dir() -> Path:
    """
    Supports both folder names:
    - Data/mock_systems
    - data/mock_systems
    """
    candidates = [
        BASE_DIR / "Data" / "mock_systems",
        BASE_DIR / "data" / "mock_systems",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Could not find mock system data folder. Expected Data/mock_systems or data/mock_systems."
    )


def _load_json(filename: str) -> Any:
    file_path = _get_data_dir() / filename

    if not file_path.exists():
        raise FileNotFoundError(f"Required mock data file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


def get_asset_context(asset_id: str) -> dict:
    assets = _load_json("assets.json")
    asset = assets.get(asset_id)

    if not asset:
        raise ValueError(f"Asset not found for asset_id={asset_id}")

    return asset


def get_telemetry(asset_id: str) -> dict:
    telemetry = _load_json("telemetry.json")
    asset_telemetry = telemetry.get(asset_id)

    if not asset_telemetry:
        raise ValueError(f"Telemetry not found for asset_id={asset_id}")

    return asset_telemetry


def get_historical_work_orders(asset_id: str, alarm_code: str) -> list[dict]:
    work_orders = _load_json("historical_work_orders.json")

    return [
        item
        for item in work_orders
        if item.get("asset_id") == asset_id
        and item.get("alarm_code") == alarm_code
    ]


def get_spare_parts(asset_id: str) -> list[dict]:
    spare_parts = _load_json("spare_parts.json")
    return spare_parts.get(asset_id, [])


def build_base_payload(
    technician_issue: str,
    asset_id: str,
    alarm_code: str,
) -> dict:
    return {
        "technician_issue": technician_issue,
        "asset_id": asset_id,
        "alarm_code": alarm_code,
        "asset_context": get_asset_context(asset_id),
        "telemetry": get_telemetry(asset_id),
        "historical_work_orders": get_historical_work_orders(asset_id, alarm_code),
        "spare_parts": get_spare_parts(asset_id),
    }


def _get_project_client() -> AIProjectClient:
    project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")

    if not project_endpoint:
        raise ValueError("FOUNDRY_PROJECT_ENDPOINT is not configured in .env.")

    return AIProjectClient(
        endpoint=project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )


def call_foundry_agent(
    project_client: AIProjectClient,
    agent_name: str,
    prompt: str,
) -> str:
    if not agent_name:
        raise ValueError("Agent name is missing.")

    openai_client = project_client.get_openai_client(agent_name=agent_name)

    response = openai_client.responses.create(
        input=prompt,
        store=True,
    )

    return response.output_text


def run_agentic_workflow(
    technician_issue: str,
    asset_id: str,
    alarm_code: str,
) -> dict:
    alarm_triage_agent = os.getenv(
        "ALARM_TRIAGE_AGENT_NAME",
        "manufacturing-alarm-triage-agent",
    )
    knowledge_agent = os.getenv(
        "KNOWLEDGE_AGENT_NAME",
        "manufacturing-knowledge-agent",
    )
    safety_agent = os.getenv(
        "SAFETY_AGENT_NAME",
        "manufacturing-safety-validation-agent",
    )
    troubleshooting_agent = os.getenv(
        "TROUBLESHOOTING_AGENT_NAME",
        "manufacturing-troubleshooting-agent",
    )

    base_payload = build_base_payload(
        technician_issue=technician_issue,
        asset_id=asset_id,
        alarm_code=alarm_code,
    )

    payload_text = json.dumps(base_payload, indent=2)

    project_client = _get_project_client()

    triage_prompt = f"""
You are the alarm triage step in a manufacturing maintenance workflow.

Read the technician issue and plant context below.

Return only valid JSON with this schema:
{{
  "asset_id": "...",
  "asset_name": "...",
  "line": "...",
  "alarm_code": "...",
  "symptom": "...",
  "severity": "low | medium | high",
  "requires_safety_validation": true,
  "data_needed": []
}}

Default asset_id to PL4-CONV-001 when the prompt mentions Packaging Line 4.
Default asset_name to Packaging Line 4 Conveyor.
Default alarm_code to E-217 when the prompt mentions alarm E-217.
Use high severity when a production line is stopped or repeatedly stopping.

Technician payload:
{payload_text}
"""

    triage_result = call_foundry_agent(
        project_client=project_client,
        agent_name=alarm_triage_agent,
        prompt=triage_prompt,
    )

    knowledge_prompt = f"""
You are the manufacturing maintenance knowledge step.

Use your connected Foundry IQ knowledge base to retrieve relevant knowledge from:
- alarm code guides
- OEM manuals
- lockout/tagout SOPs
- known-error documents
- preventive maintenance checklists

For the given asset, alarm code, and symptom, return:
1. Relevant alarm meaning
2. Required safety procedures
3. Recommended diagnostic order
4. Known similar issues
5. Source-grounded notes

Do not invent plant-specific information.
If knowledge is missing, say what is missing.

Technician payload:
{payload_text}

Triage result:
{triage_result}
"""

    knowledge_result = call_foundry_agent(
        project_client=project_client,
        agent_name=knowledge_agent,
        prompt=knowledge_prompt,
    )

    safety_prompt = f"""
You are the safety validation step in a manufacturing troubleshooting workflow.

Review the technician issue, plant context, triage result, and Foundry IQ knowledge result.

Rules:
- Any physical inspection of conveyors, motors, belts, photo-eye sensors, brackets, guards, or rotating equipment must include lockout/tagout.
- Do not allow bypassing sensors, interlocks, or guards unless supervisor approval is explicitly required.
- If safety information is missing, mark approval_required as true.

Return structured JSON with:
{{
  "safety_risk": true,
  "required_safety_steps": [],
  "blocked_actions": [],
  "approval_required": true,
  "safety_summary": "..."
}}

Technician payload:
{payload_text}

Triage result:
{triage_result}

Foundry IQ knowledge result:
{knowledge_result}
"""

    safety_result = call_foundry_agent(
        project_client=project_client,
        agent_name=safety_agent,
        prompt=safety_prompt,
    )

    troubleshooting_prompt = f"""
You are the final senior maintenance troubleshooting agent.

Use all provided evidence to generate the final answer.

Use:
- Technician issue
- Asset context
- Telemetry
- Historical work orders
- Spare parts
- Alarm triage result
- Foundry IQ knowledge result
- Safety validation result

Return the final answer in this structure:
1. Situation Summary
2. Evidence Reviewed
3. Likely Causes Ranked
4. Safe First Checks
5. Recommended Corrective Action
6. Spare Parts to Check
7. Draft Work Order
8. Confidence and Assumptions

Always prioritize safety.
If physical inspection is recommended, include lockout/tagout.
Do not invent facts.

Technician payload:
{payload_text}

Triage result:
{triage_result}

Foundry IQ knowledge result:
{knowledge_result}

Safety validation result:
{safety_result}
"""

    final_recommendation = call_foundry_agent(
        project_client=project_client,
        agent_name=troubleshooting_agent,
        prompt=troubleshooting_prompt,
    )

    return {
        "input_payload": base_payload,
        "triage_result": triage_result,
        "knowledge_result": knowledge_result,
        "safety_result": safety_result,
        "final_recommendation": final_recommendation,
        "agents": {
            "alarm_triage_agent": alarm_triage_agent,
            "knowledge_agent": knowledge_agent,
            "safety_agent": safety_agent,
            "troubleshooting_agent": troubleshooting_agent,
        },
    }