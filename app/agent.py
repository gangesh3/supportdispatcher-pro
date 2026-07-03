# ruff: noqa
import os
import json
import re
import datetime
import sys
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Generator

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.workflow import Workflow, Edge, START, FunctionNode, node
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from app.config import config

# -----------------------------------------------------------------------------
# 1. Input / Output Schemas & State Definition
# -----------------------------------------------------------------------------

class TicketRequest(BaseModel):
    ticket_text: str = Field(description="The body text of the customer support ticket.")
    customer_email: str = Field(description="The customer's email address.")

class OrchestratorOutput(BaseModel):
    customer_email: str = Field(description="The validated customer email.")
    customer_tier: str = Field(description="The looked up customer subscription tier (basic or premium).")
    draft_response: str = Field(description="The draft support reply generated for the ticket.")
    reasoning: str = Field(description="Reasoning explaining the triage or delegation decision.")

class FinalResponse(BaseModel):
    status: str = Field(description="Workflow status: approved, auto_approved, rejected, or error.")
    customer_email: str = Field(description="The customer email address.")
    customer_tier: str = Field(description="The customer subscription tier.")
    response_text: str = Field(description="The final support response text.")
    audit_log: List[dict] = Field(default_factory=list, description="Structured audit log entries.")

class TicketState(BaseModel):
    ticket_text: str = ""
    customer_email: str = ""
    customer_tier: str = "basic"
    draft_response: str = ""
    security_status: str = "clean"
    audit_log: List[dict] = Field(default_factory=list)
    approval_status: str = "pending"

# -----------------------------------------------------------------------------
# 2. Specialized LLM Agents & Orchestrator
# -----------------------------------------------------------------------------

# Stdio connection to local mcp_server.py
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"]
        )
    )
)

tier_lookup_agent = LlmAgent(
    name="tier_lookup_agent",
    model=Gemini(model=config.model),
    instruction=(
        "You are a customer tier lookup assistant. "
        "Your task is to find the customer's subscription tier (basic or premium) and recent ticket history using your tools. "
        "If no tools are available or the database returns nothing, assume the tier is 'basic'. "
        "Return a clear, concise summary of the tier and ticket history."
    ),
    tools=[mcp_toolset]
)

response_drafter_agent = LlmAgent(
    name="response_drafter_agent",
    model=Gemini(model=config.model),
    instruction=(
        "You are a customer support response drafting assistant. "
        "Your task is to generate a helpful, polite support response draft to the customer's query. "
        "Tailor the response based on the customer's subscription tier (basic or premium) and historical context if available. "
        "If the customer is premium, keep the tone extra professional. "
        "Use your tools (like get_internal_knowledge) to lookup relevant company policies."
    ),
    tools=[mcp_toolset]
)

triage_orchestrator = LlmAgent(
    name="triage_orchestrator",
    model=Gemini(model=config.model),
    instruction=(
        "You are the main support triage coordinator. "
        "Given the customer support ticket text and customer email, you must perform two tasks: "
        "1. Delegate to the tier_lookup_agent using its tool to check the customer's subscription tier (basic or premium) and ticket history. "
        "2. Once you have the tier and history, delegate to the response_drafter_agent using its tool to draft a polite response. "
        "You must return the customer's email, customer's tier, and the draft_response in structured format. "
        "Explain your reasoning for the drafted response."
    ),
    tools=[
        AgentTool(agent=tier_lookup_agent),
        AgentTool(agent=response_drafter_agent)
    ],
    output_schema=OrchestratorOutput
)

# -----------------------------------------------------------------------------
# 3. Workflow Node Logic
# -----------------------------------------------------------------------------

@node
def security_checkpoint(ctx: Context, node_input: TicketRequest) -> Event:
    raw_text = node_input.ticket_text
    email = node_input.customer_email
    
    # 1. Scrub PII (Sensitive patterns)
    scrubbed_text = raw_text
    cc_pattern = r'\b(?:\d[ -]*?){13,16}\b'
    scrubbed_text = re.sub(cc_pattern, "[REDACTED_CARD_NUMBER]", scrubbed_text)
    key_pattern = r'\b[A-Za-z0-9-_]{32,}\b'
    scrubbed_text = re.sub(key_pattern, "[REDACTED_API_KEY]", scrubbed_text)
    
    # 2. Prompt injection check
    injection_keywords = ["ignore previous instructions", "system prompt", "you must instead", "bypass security"]
    is_injection = False
    for kw in injection_keywords:
        if kw in scrubbed_text.lower():
            is_injection = True
            break
            
    # 3. Domain-specific rule (blocked/invalid domains check)
    blocked_domains = ["tempmail.com", "dispostable.com", "spam.com"]
    is_blocked_domain = False
    if "@" in email:
        domain = email.split("@")[-1].lower().strip()
        if domain in blocked_domains:
            is_blocked_domain = True
    else:
        is_blocked_domain = True  # Invalid email format
        
    event_severity = "INFO"
    event_message = "Input scanned successfully."
    
    if scrubbed_text != raw_text:
        event_severity = "WARNING"
        event_message = "PII elements redacted."
        
    if is_injection:
        event_severity = "CRITICAL"
        event_message = "Prompt injection attempt detected!"
    elif is_blocked_domain:
        event_severity = "CRITICAL"
        event_message = f"Email domain block/validation triggered for: {email}."
        
    audit_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "node": "security_checkpoint",
        "severity": event_severity,
        "message": event_message,
        "customer_email": email
    }
    
    # Write to local JSON audit log file
    try:
        with open("security_audit.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(audit_entry) + "\n")
    except Exception:
        pass
    
    current_audit = list(ctx.state.get("audit_log", [])) if ctx.state else []
    current_audit.append(audit_entry)
    
    is_flagged = is_injection or is_blocked_domain
    
    if is_flagged:
        return Event(
            output="Security threat or policy violation detected.",
            route="SECURITY_EVENT",
            state={
                "ticket_text": scrubbed_text,
                "customer_email": email,
                "security_status": "flagged",
                "audit_log": current_audit
            }
        )
    else:
        return Event(
            output=f"Customer Email: {email}\nTicket Content: {scrubbed_text}",
            route="clean",
            state={
                "ticket_text": scrubbed_text,
                "customer_email": email,
                "security_status": "clean",
                "audit_log": current_audit
            }
        )

@node
def check_tier_routing(ctx: Context, node_input: OrchestratorOutput) -> Event:
    customer_email = node_input.customer_email
    customer_tier = node_input.customer_tier.strip().lower()
    draft_response = node_input.draft_response
    
    audit_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "node": "check_tier_routing",
        "severity": "INFO",
        "message": f"Orchestration completed. Customer: {customer_email}, Tier: {customer_tier}."
    }
    current_audit = list(ctx.state.get("audit_log", []))
    current_audit.append(audit_entry)
    
    route = "premium" if customer_tier == "premium" else "basic"
    
    return Event(
        output=draft_response,
        route=route,
        state={
            "customer_email": customer_email,
            "customer_tier": customer_tier,
            "draft_response": draft_response,
            "audit_log": current_audit
        }
    )

@node(rerun_on_resume=True)
async def approval_checkpoint(ctx: Context, node_input: str) -> Generator[Any, Any, Any]:
    if not ctx.resume_inputs or "human_approval" not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="human_approval",
            message=f"Premium Customer Response Approval Required.\nDrafted Response:\n{node_input}\n\nDo you approve? (approved / denied or edit response)"
        )
        return
        
    human_decision = ctx.resume_inputs["human_approval"].strip()
    status = "approved"
    final_text = node_input
    
    if human_decision.lower() == "denied":
        status = "rejected"
        final_text = "Support request denied by human reviewer."
    elif human_decision.lower() != "approved":
        status = "approved"
        final_text = human_decision
        
    audit_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "node": "approval_checkpoint",
        "severity": "INFO",
        "message": f"Human review completed with decision: {status}."
    }
    current_audit = list(ctx.state.get("audit_log", []))
    current_audit.append(audit_entry)
    
    yield Event(
        output=FinalResponse(
            status=status,
            customer_email=ctx.state.get("customer_email"),
            customer_tier=ctx.state.get("customer_tier"),
            response_text=final_text,
            audit_log=current_audit
        ),
        state={
            "approval_status": status,
            "audit_log": current_audit
        }
    )

@node
def auto_approve(ctx: Context, node_input: str) -> Event:
    audit_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "node": "auto_approve",
        "severity": "INFO",
        "message": "Response auto-approved for basic tier customer."
    }
    current_audit = list(ctx.state.get("audit_log", []))
    current_audit.append(audit_entry)
    
    return Event(
        output=FinalResponse(
            status="auto_approved",
            customer_email=ctx.state.get("customer_email"),
            customer_tier=ctx.state.get("customer_tier"),
            response_text=node_input,
            audit_log=current_audit
        ),
        state={
            "approval_status": "auto_approved",
            "audit_log": current_audit
        }
    )

@node
def security_event_handler(ctx: Context, node_input: str) -> Event:
    audit_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "node": "security_event_handler",
        "severity": "CRITICAL",
        "message": "Security policy violation: ticket rejected."
    }
    current_audit = list(ctx.state.get("audit_log", []))
    current_audit.append(audit_entry)
    
    return Event(
        output=FinalResponse(
            status="rejected",
            customer_email=ctx.state.get("customer_email"),
            customer_tier="unknown",
            response_text="Ticket rejected due to safety policy violation.",
            audit_log=current_audit
        ),
        state={
            "approval_status": "rejected",
            "audit_log": current_audit
        }
    )

@node
def final_output(ctx: Context, node_input: FinalResponse) -> Generator[Any, Any, Any]:
    ui_text = (
        f"### Ticket Dispatch Result\n"
        f"**Status**: {node_input.status.upper()}\n"
        f"**Customer**: {node_input.customer_email} ({node_input.customer_tier})\n\n"
        f"**Response Draft**:\n{node_input.response_text}\n\n"
        f"--- \n"
        f"**Security & Process Audit Logs**:\n"
    )
    for log in node_input.audit_log:
        ui_text += f"- [{log['severity']}] {log['node']}: {log['message']}\n"
        
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=ui_text)]))
    yield Event(output=node_input)

# -----------------------------------------------------------------------------
# 4. Workflow Assembly & App Export
# -----------------------------------------------------------------------------

root_agent = Workflow(
    name="supportdispatcher_workflow",
    description="Automated CRM Lead / Support ticket router with MCP and safety checks",
    input_schema=TicketRequest,
    output_schema=FinalResponse,
    state_schema=TicketState,
    edges=[
        Edge(from_node=START, to_node=security_checkpoint),
        Edge(from_node=security_checkpoint, to_node=triage_orchestrator, route="clean"),
        Edge(from_node=security_checkpoint, to_node=security_event_handler, route="SECURITY_EVENT"),
        Edge(from_node=triage_orchestrator, to_node=check_tier_routing),
        Edge(from_node=check_tier_routing, to_node=approval_checkpoint, route="premium"),
        Edge(from_node=check_tier_routing, to_node=auto_approve, route="basic"),
        Edge(from_node=approval_checkpoint, to_node=final_output),
        Edge(from_node=auto_approve, to_node=final_output),
        Edge(from_node=security_event_handler, to_node=final_output),
    ]
)

app = App(
    root_agent=root_agent,
    name="app",
)
