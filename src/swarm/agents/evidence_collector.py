import asyncio
import os
import json
import re
from typing import Dict, Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from dotenv import load_dotenv

from src.swarm.state.schema import AuditState

load_dotenv()

# Mapping of Control Domain Prefixes to LOCAL MCP Tool Names
AUDIT_TOOL_MAP = {
    "AC": ["get_iam_password_policy", "list_iam_users_with_mfa"],
    "CST": ["list_public_s3_buckets"],
    "CLD": ["list_public_s3_buckets"],
}


async def collect_aws_evidence(state: AuditState) -> Dict[str, Any]:
    """
    LangGraph Node: Evidence Collector (Local MCP version)
    Connects to the local Python MCP server and pulls real evidence.
    """
    print(
        "\n[Evidence Collector] starting live AWS data gathering via Local MCP sidecar..."
    )

    # Define local server parameters
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "src.swarm.mcp_server"],
        env=os.environ.copy(),
    )

    evidence_updates = {}

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Identify which domains we need to scan based on the scope
                domains_to_scan = set()
                for item in state.control_matrix:
                    prefix = item.control_id.split("-")[0]
                    if prefix in AUDIT_TOOL_MAP:
                        domains_to_scan.add(prefix)

                print(
                    f"[Evidence Collector] Detected {len(domains_to_scan)} domains to scan: {domains_to_scan}"
                )

                for prefix in domains_to_scan:
                    tool_names = AUDIT_TOOL_MAP[prefix]
                    for tool_name in tool_names:
                        print(f"  -> Calling MCP Tool: {tool_name}...")

                        try:
                            result = await session.call_tool(tool_name)

                            if result and result.content:
                                raw_text = result.content[0].text
                                try:
                                    data = json.loads(raw_text)
                                    # Scrub sensitive Account IDs (12 digits) from keys and values
                                    redacted_data = _redact_aws_metadata(data)
                                    evidence_updates[prefix] = redacted_data
                                except Exception:
                                    evidence_updates[prefix] = _redact_aws_metadata(
                                        raw_text
                                    )

                        except Exception as tool_err:
                            print(f"  ! Tool call failed for {tool_name}: {tool_err}")
                            evidence_updates[prefix] = (
                                f"Error gathering evidence: {tool_err}"
                            )

    except Exception as e:
        print(f"❌ Critical error in Local MCP Evidence Collector: {e}")

    # Merge updates into the state evidence log
    new_log = state.evidence_log.copy()
    for prefix, data in evidence_updates.items():
        # Map back to specific controls that share this prefix the simple way
        for item in state.control_matrix:
            if item.control_id.startswith(prefix):
                new_log[item.control_id] = data

    return {"evidence_log": new_log}


def _redact_aws_metadata(data: Any) -> Any:
    """Recursively redacts 12-digit AWS Account IDs from strings and dictionary trees."""
    account_id_pattern = re.compile(r"\d{12}")

    if isinstance(data, str):
        return account_id_pattern.sub("XXXXXXXXXXXX", data)
    elif isinstance(data, list):
        return [_redact_aws_metadata(item) for item in data]
    elif isinstance(data, dict):
        return {k: _redact_aws_metadata(v) for k, v in data.items()}
    return data


def run_evidence_collector_sync(state: AuditState) -> dict:
    """Wrapper for LangGraph sync execution."""
    return asyncio.run(collect_aws_evidence(state))
