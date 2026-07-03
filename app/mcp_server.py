# ruff: noqa
import sys
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP Server
mcp = FastMCP("SupportDispatcherPro MCP Server")

@mcp.tool()
def get_customer_tier(email: str) -> str:
    """Gets the subscription tier for a customer by email.

    Args:
        email: The customer's email address.
    """
    email_lower = email.lower().strip()
    # Simple mock lookup database
    if "premium" in email_lower or email_lower.endswith("@google.com") or "vip" in email_lower:
        return "premium"
    return "basic"

@mcp.tool()
def get_customer_history(email: str) -> str:
    """Gets recent support ticket summaries for a customer.

    Args:
        email: The customer's email address.
    """
    email_lower = email.lower().strip()
    if "premium" in email_lower or email_lower.endswith("@google.com"):
        return (
            "1. ticket_id: #1001, status: resolved, subject: 'API timeout issues in production'\n"
            "2. ticket_id: #1002, status: resolved, subject: 'Need custom SLA terms for contract'"
        )
    elif "vip" in email_lower:
        return (
            "1. ticket_id: #2001, status: resolved, subject: 'Requesting access to beta LLM features'"
        )
    else:
        return (
            "1. ticket_id: #3001, status: resolved, subject: 'Password reset link not working'\n"
            "2. ticket_id: #3002, status: closed, subject: 'Refund query for basic plan'"
        )

@mcp.tool()
def get_internal_knowledge(topic: str) -> str:
    """Retrieves standard customer support answers or guidelines for a specific topic.

    Args:
        topic: The support topic to retrieve answers for (e.g. 'refunds', 'slas', 'api', 'beta').
    """
    topic_lower = topic.lower().strip()
    if "refund" in topic_lower:
        return (
            "REFUND POLICY:\n"
            "- Basic users: 14-day money-back guarantee, subject to 5% processing fee.\n"
            "- Premium users: No-questions-asked refund within 30 days of purchase.\n"
            "- No refunds for custom enterprise contracts once SLA period starts."
        )
    elif "sla" in topic_lower or "contract" in topic_lower:
        return (
            "SERVICE LEVEL AGREEMENT (SLA):\n"
            "- Basic users: Standard email support, best-effort reply time (usually 24-48 business hours).\n"
            "- Premium users: 24/7 dedicated support with a guaranteed response within 2 hours for P1 issues."
        )
    elif "api" in topic_lower:
        return (
            "API DOCUMENTATION:\n"
            "- Production endpoint: https://api.supportdispatcher.pro/v1\n"
            "- Rate limits: 100 req/min for Basic, 5000 req/min for Premium.\n"
            "- To report production API issues, escalate to the Devops channel."
        )
    elif "beta" in topic_lower or "feature" in topic_lower:
        return (
            "BETA PROGRAMS:\n"
            "- Basic users: Eligible only for public beta programs.\n"
            "- Premium users: Automatic enrollment in early-access beta features upon request."
        )
    else:
        return (
            "GENERAL GUIDELINE:\n"
            "- Always thank the customer for contacting support.\n"
            "- Keep a friendly, professional, and empathetic tone.\n"
            "- If unable to resolve the ticket, mention that we are escalating it."
        )

if __name__ == "__main__":
    # Runs the stdio MCP server loop
    mcp.run()
