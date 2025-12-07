# ****************************************
# AI Agent
# Date : 05/12/2025
# Description : 
# Author : ALI NAAMA / AI 	
#
# ***************************************

import requests
import os
import json
from typing import TypedDict, Annotated, Sequence, Optional
from msal import ConfidentialClientApplication
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, BaseMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin
import msal
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel as FastAPIBaseModel
import uvicorn
import re
from datetime import datetime
from datetime import datetime, timedelta


# Global variable token
# Dynamics API setup
# Dynamics API setup (replace with your values; use env vars in prod)
tenant_id = "4edxxxxxxxxxxxxxx8ffaf1"
client_id = "4edxxxxxxxxxxxxxx8ffaf1d38"
client_secret = "4edxxxxxxxxxxxxxx8ffaf15l0c8dTAnhafa"
TENANTNAME = 'orgd7c4edxxxxxxxxxxxxxx8ffaf17'
CLIENT_ID = "4edxxxxxxxxxxxxxx8ffaf1147ff3dd38"
CLIENT_SECRET = "8D4edxxxxxxxxxxxxxx8ffaf1TAnhafa"

DYNAMICS_URL = f"https://{TENANTNAME}.crm12.dynamics.com"
dynamics_url = f"https://{TENANTNAME}.crm12.dynamics.com"
authority = f"https://login.microsoftonline.com/{tenant_id}"
scope = [f"{dynamics_url}/.default"]

print(f"Initializing Dynamics connection to: {DYNAMICS_URL}")

# Global token and headers
token = None
headers = {}

# Local LLM (assumes Ollama/vLLM running locally)
try:
    # Verify your llm running status and port !!!!!
    llm = ChatOllama(model="llama3.1:8b", base_url="http://localhost:11434")
    print(llm.model)
    print("LLM initialized successfully")
except Exception as e:
    print(f"Warning: LLM initialization failed: {e}")


    # Create a mock LLM for testing
    class MockLLM:
        def invoke(self, prompt):
            class MockResponse:
                content = json.dumps({
                    "steps": ["1. Verify customer identity", "2. Update address in system",
                              "3. Send confirmation email"],
                    "email_draft": "Dear customer, we have successfully updated your account address as requested. Please verify the changes are correct. Thank you.",
                    "confidence": 85,
                    "escalate": False
                })

            return MockResponse()


    llm = MockLLM()


def get_dynamics_token():
    """Get or refresh Dynamics token"""
    global token, headers
    try:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=authority,
            client_credential=client_secret
        )

        result = app.acquire_token_for_client(scopes=scope)

        if "access_token" not in result:
            print(f"Auth failed: {result.get('error_description')}")
            token = None
        else:
            token = result["access_token"]
            headers = {
                "Authorization": f"Bearer {token}" if token else "",
                "Content-Type": "application/json",
                "OData-MaxVersion": "4.0",
                "OData-Version": "4.0"
            }
            print("Dynamics token acquired successfully")
            return token
    except Exception as e:
        print(f"Token acquisition failed: {e}")
        token = None
    return None


# Initialize token
get_dynamics_token()


# Pydantic models
class ResolutionOutput(BaseModel):
    steps: list[str] = Field(..., description="Suggested resolution steps")
    email_draft: str = Field(..., description="Draft customer email/SMS")
    confidence: int = Field(..., ge=0, le=100, description="Confidence score (0-100)")
    escalate: bool = Field(default=False, description="True if escalation recommended")


# Agent state
class AgentState(TypedDict):
    case_id: str
    task: str
    messages: Annotated[Sequence[BaseMessage], "add"]
    case_data: Optional[dict]
    resolution: Optional[ResolutionOutput]
    escalate: bool
    next_action: str
    tool_result: Optional[str]


# Tools
@tool
def fetch_case_data(case_id: str) -> dict:
    """Fetch Dynamics incident (case) data."""
    print(f"fetch_case_data called with case_id: {case_id}")
    # set up Token
    access_token = token
    base_url = DYNAMICS_URL

    # URL Construction
    api_version = "v9.2"
    api_path = f"/api/data/{api_version}/incidents({case_id})"
    query = (
        "?$select=incidentid,title,prioritycode,statuscode,statecode,createdon,description&"
        "$expand=customerid_account($select=accountid,name),customerid_contact($select=contactid,fullname)"
    )

    url = urljoin(base_url, api_path + query)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0"
    }

    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        print(f"Fetching case {case_id} from: {url}")
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        print(f"Successfully fetched case data for {case_id}")
        #return data
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error fetching case {case_id}: {e}")
        raise
    finally:
        #session.close()
        print('end query Case Id')

    # For testing, return mock data
    return {
        "incidentid": case_id,
        "title": data.get('title', 'N/A'),
        "description": data.get('description', 'N/A'),
        "prioritycode": data.get('prioritycode', '1'),
        "statuscode": data.get('statuscode', '1'),
        "statecode": data.get('statecode', '1'),
        "createdon": "2025-01-15T10:30:00Z",
        "customerid_account": {
            "accountid": 'GEVIDREAM',
            "name": "TEST"
        },
        "customerid_contact": None
    }


def create_task_for_case(case_id: str, task_subject: str, task_description: str = None,
                         due_date: datetime = None, priority: int = 1) -> dict:
    """
    Create a task (activitypointer) in Dynamics 365 and associate it with a case.

    Args:
        case_id: The Dynamics incident ID (guid)
        task_subject: Subject/title of the task
        task_description: Detailed description of the task
        due_date: Due date for the task (datetime object)
        priority: Priority code (0=Low, 1=Normal, 2=High)

    Returns:
        dict: Response from Dynamics API with created task data
    """
    print(f"create_task_for_case called for case_id: {case_id}")

    # Set up authentication and URL
    access_token = token
    base_url = DYNAMICS_URL
    api_version = "v9.2"

    # First, verify the case exists by trying to fetch it
    try:
        # Simple case verification
        verify_url = urljoin(base_url, f"/api/data/{api_version}/incidents({case_id})?$select=title")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0"
        }

        session = requests.Session()
        retry_strategy = Retry(
            total=2,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        print(f"Verifying case exists: {case_id}")
        verify_response = session.get(verify_url, headers=headers, timeout=15)

        if verify_response.status_code == 404:
            print(f"Case {case_id} not found in Dynamics")
            return {"error": f"Case {case_id} not found"}

        verify_response.raise_for_status()
        case_data = verify_response.json()
        case_title = case_data.get('title', 'Unknown Case')
        print(f"✓ Case found: {case_title}")

    except requests.exceptions.HTTPError as e:
        print(f"HTTP error verifying case: {e}")
        return {"error": f"Case verification failed: {e}"}
    except Exception as e:
        print(f"Error verifying case: {e}")
        # Continue anyway - maybe the case exists but we can't verify

    # Endpoint for creating tasks
    endpoint = f"/api/data/{api_version}/tasks"
    url = urljoin(base_url, endpoint)

    # Headers for creation
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Prefer": "return=representation"  # Returns the created entity
    }

    # Prepare task data
    task_data = {
        "subject": task_subject,
        "description": task_description or f"Task related to case: {case_id}",
        "prioritycode": priority,
        "scheduledstart": datetime.now().isoformat() + "Z",
    }

    # Add due date if provided
    if due_date:
        task_data["scheduledend"] = due_date.isoformat() + "Z"
    else:
        # Default: due in 7 days
        default_due = datetime.now() + timedelta(days=7)
        task_data["scheduledend"] = default_due.isoformat() + "Z"

    # Associate with the case
    task_data["regardingobjectid_incident@odata.bind"] = f"/incidents({case_id})"

    # Create new session for task creation
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    try:
        print(f"Creating task for case {case_id}")
        print(f"Task subject: {task_subject}")
        print(f"Priority: {priority}")

        response = session.post(url, headers=headers, json=task_data, timeout=30)
        response.raise_for_status()

        created_task = response.json()
        task_id = created_task.get("activityid")

        print(f"✓ Task created successfully: {task_id}")
        print(f"Task subject: {created_task.get('subject')}")
        print(f"Due date: {created_task.get('scheduledend', 'Not set')}")

        return created_task

    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error creating task: {e}"
        print(error_msg)
        if e.response:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text[:500]}")
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"Error creating task: {e}"
        print(error_msg)
        return {"error": error_msg}
    finally:
        session.close()
        print('Task creation process completed')


# Alternative version if you have a working fetch_case_data function
def create_task_for_case_with_fallback(case_id: str, task_subject: str, task_description: str = None,
                                       due_date: datetime = None, priority: int = 1) -> dict:
    """
    Create a task with fallback logic that doesn't depend on fetch_case_data
    """
    print(f"Creating task for case: {case_id}")

    # Use the simple version above
    return create_task_for_case(case_id, task_subject, task_description, due_date, priority)


# Simple test function
def test_create_task():
    """Test the task creation function"""

    # Replace with your actual test case ID
    test_case_id = "02888c89-4fc8-f011-8543-002248da5d02"

    print("Testing task creation...")

    result = create_task_for_case(
        case_id=test_case_id,
        task_subject="Test Task - Follow up required",
        task_description="This is a test task created via the API. Please review the case details and contact the customer.",
        due_date=datetime.now() + timedelta(days=3),
        priority=1  # Normal priority
    )

    if "error" in result:
        print(f"❌ Task creation failed: {result['error']}")

        # Try with a simpler approach
        print("\nTrying alternative approach...")
        result = create_simple_task(test_case_id, "Test Task")

    else:
        print(f"✅ Task created successfully!")
        print(f"Task ID: {result.get('activityid', 'Unknown')}")
        print(f"Subject: {result.get('subject', 'Unknown')}")

    return result


def create_simple_task(case_id: str, task_subject: str) -> dict:
    """
    Minimal task creation function with hardcoded values for testing
    """
    print(f"Creating simple task for case: {case_id}")

    access_token = token
    base_url = DYNAMICS_URL
    api_version = "v9.2"

    endpoint = f"/api/data/{api_version}/tasks"
    url = urljoin(base_url, endpoint)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation"
    }

    # Very simple task data
    task_data = {
        "subject": task_subject,
        "description": f"Task automatically created for case {case_id}",
        "prioritycode": 1,
        "scheduledstart": datetime.now().isoformat() + "Z",
        "scheduledend": (datetime.now() + timedelta(days=7)).isoformat() + "Z",
        "regardingobjectid_incident@odata.bind": f"/incidents({case_id})"
    }

    try:
        session = requests.Session()
        response = session.post(url, headers=headers, json=task_data, timeout=30)
        response.raise_for_status()

        result = response.json()
        print(f"✓ Simple task created: {result.get('activityid', 'Unknown')}")
        return result

    except Exception as e:
        print(f"✗ Simple task creation failed: {e}")
        return {"error": str(e)}


@tool
def update_case_resolved(case_id: str, resolution: dict) -> str:
    """Update case as resolved with note."""
    print(f"update_case_resolved called for case {case_id}")

    # For testing, return mock response
    return f"Case {case_id} marked as resolved. Email draft sent to customer. Confidence: {resolution.get('confidence', 0)}%"


@tool
def escalate_case(case_id: str, resolution: dict) -> str:
    """Escalate case: Reassign, add note, increase priority."""
    print(f"escalate_case called for case {case_id}")

    # For testing, return mock response
    return f"Case {case_id} escalated to senior support. Low confidence: {resolution.get('confidence', 0)}%"


# Node functions
def fetch_node(state: AgentState) -> AgentState:
    """Fetch case data."""
    print(f"Fetching data for case: {state['case_id']}")
    try:
        result = fetch_case_data.invoke({"case_id": state["case_id"]})
        state["case_data"] = result

        # Create tool message for the fetch action
        tool_call_id = f"fetch_{state['case_id'][:8]}"
        state["messages"] = state.get("messages", []) + [
            HumanMessage(content=f"Please fetch case {state['case_id']}"),
            AIMessage(
                content="I'll fetch the case data.",
                tool_calls=[{
                    "name": "fetch_case_data",
                    "args": {"case_id": state["case_id"]},
                    "id": tool_call_id
                }]
            ),
            ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=tool_call_id,
                name="fetch_case_data"
            )
        ]
        print(f"Successfully fetched case {state['case_id']}")

    except Exception as e:
        print(f"Error fetching case {state['case_id']}: {e}")
        state["case_data"] = {}
        state["escalate"] = True
        state["messages"] = state.get("messages", []) + [
            HumanMessage(content=f"Fetch case {state['case_id']}"),
            AIMessage(content=f"Error fetching case: {str(e)}")
        ]

    return state


def analyze_node(state: AgentState) -> AgentState:
    """Generate resolution using LLM."""
    print(f"Analyzing case: {state['case_id']}")

    if not state.get("case_data"):
        print(f"No case data found for {state['case_id']}")
        state["escalate"] = True
        state["resolution"] = ResolutionOutput(
            steps=["Error: Case data not found"],
            email_draft="Unable to generate draft due to missing case data.",
            confidence=0,
            escalate=True
        )
        return state

    case = state["case_data"]

    # Extract case info
    try:
        title = case.get('title', 'N/A')
        description = case.get('description', 'N/A')

        customer_info = 'N/A'
        if 'customerid_account' in case and case['customerid_account']:
            customer_info = case['customerid_account'].get('name', 'N/A')
        elif 'customerid_contact' in case and case['customerid_contact']:
            customer_info = case['customerid_contact'].get('fullname', 'N/A')

        priority = case.get('prioritycode', 'N/A')
        status = case.get('statuscode', 'N/A')

    except Exception as e:
        print(f"Error extracting case info: {e}")
        title = 'N/A'
        description = 'N/A'
        customer_info = 'N/A'
        priority = 'N/A'
        status = 'N/A'

    # Prompt for LLM
    prompt = f"""
    Analyze this customer service case and provide resolution steps:

    Case Title: {title}
    Description: {description}
    Customer: {customer_info}
    Priority: {priority}
    Status: {status}

    Task: {state.get('task', 'Suggest resolution')}

    Provide response in this exact JSON format:
    {{
        "steps": ["step1", "step2", "step3"],
        "email_draft": "draft email text here",
        "confidence": 85,
        "escalate": false
    }}

    If confidence is below 70 or it's a high-risk case, set escalate to true.
    """

    try:
        print(f"Calling LLM for case analysis: {state['case_id']}")
        response = llm.invoke(prompt)
        response_text = response.content
        print(f"LLM response received: {response_text[:100]}...")

        # Extract JSON
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)

        output_dict = json.loads(response_text)
        resolution = ResolutionOutput(**output_dict)

        state["resolution"] = resolution
        state["escalate"] = resolution.escalate or (resolution.confidence < 70)

        # Determine next action
        if state["escalate"]:
            state["next_action"] = "escalate"
            print(f"Case {state['case_id']} marked for escalation (confidence: {resolution.confidence})")
        else:
            state["next_action"] = "resolve"
            print(f"Case {state['case_id']} marked for resolution (confidence: {resolution.confidence})")


    except json.JSONDecodeError as e:
        print(f"JSON parsing error for case {state['case_id']}: {e}")
        state["resolution"] = ResolutionOutput(
            steps=["Error: Could not parse LLM response"],
            email_draft="Unable to draft due to parsing error.",
            confidence=0,
            escalate=True
        )
        state["escalate"] = True
        state["next_action"] = "escalate"
    except Exception as e:
        print(f"Error analyzing case {state['case_id']}: {e}")
        state["resolution"] = ResolutionOutput(
            steps=["Error in analysis"],
            email_draft="Unable to draft due to error.",
            confidence=0,
            escalate=True
        )
        state["escalate"] = True
        state["next_action"] = "escalate"

    return state


def action_node(state: AgentState) -> AgentState:
    """Execute the appropriate action (resolve or escalate)."""
    print(f"Taking action for case: {state['case_id']}, action: {state.get('next_action')}")

    if not state.get("next_action") or not state.get("resolution"):
        print(f"No action or resolution for case {state['case_id']}")
        state["tool_result"] = "No action to perform"
        return state

    case_id = state["case_id"]
    resolution = state["resolution"]
    next_action = state["next_action"]

    try:
        if next_action == "resolve":
            print(f"Resolving case {case_id}")
            result = update_case_resolved.invoke({
                "case_id": case_id,
                "resolution": resolution.model_dump()  # Changed from .dict() to .model_dump()
            })
            tool_name = "update_case_resolved"
        else:  # escalate
            print(f"Escalating case {case_id}")
            result = escalate_case.invoke({
                "case_id": case_id,
                "resolution": resolution.model_dump()  # Changed from .dict() to .model_dump()
            })
            tool_name = "escalate_case"

        state["tool_result"] = result
        print(f"Action result for case {case_id}: {result}")

        # Create tool message
        tool_call_id = f"{next_action}_{case_id[:8]}"
        state["messages"] = state.get("messages", []) + [
            AIMessage(
                content=f"I'll {next_action} this case.",
                tool_calls=[{
                    "name": tool_name,
                    "args": {"case_id": case_id, "resolution": resolution.model_dump()},
                    # Changed from .dict() to .model_dump()
                    "id": tool_call_id
                }]
            ),
            ToolMessage(
                content=result,
                tool_call_id=tool_call_id,
                name=tool_name
            )
        ]

    except Exception as e:
        print(f"Error taking action for case {case_id}: {e}")
        state["tool_result"] = f"Action failed: {str(e)}"

    return state


def decide_route(state: AgentState) -> str:
    """Decide which route to take after analysis."""
    if state.get("escalate", False):
        return "escalate_route"
    return "resolve_route"


# Build graph
workflow = StateGraph(AgentState)

# Add nodes
workflow.add_node("fetch", fetch_node)
workflow.add_node("analyze", analyze_node)
workflow.add_node("action", action_node)

# Set up workflow
workflow.set_entry_point("fetch")
workflow.add_edge("fetch", "analyze")

# Conditional routing based on escalate flag
workflow.add_conditional_edges(
    "analyze",
    decide_route,
    {
        "resolve_route": "action",
        "escalate_route": "action"
    }
)

workflow.add_edge("action", END)

# Compile the graph
app_graph = workflow.compile()


# FastAPI setup
class CaseInput(FastAPIBaseModel):
    case_id: str
    task: str = "Suggest resolution and check for escalation"


# Create FastAPI app with CORS
api = FastAPI(
    title="Dynamics 365 Case Resolution Agent",
    description="AI agent for resolving Dynamics 365 customer service cases",
    version="1.0.0"
)

# Add CORS middleware
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@api.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Dynamics 365 Case Resolution Agent API",
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "endpoints": {
            "POST /resolve_case": "Resolve a Dynamics 365 case",
            "GET /test_fetch/{case_id}": "Test case data fetching",
            "GET /health": "Service health check",
            "GET /test": "Simple test endpoint"
        },
        "version": "1.0.0"
    }


@api.get("/test")
async def test_endpoint():
    """Simple test endpoint to verify server is running."""
    return {
        "status": "ok",
        "message": "API server is running",
        "timestamp": datetime.now().isoformat()
    }


@api.post("/resolve_case")
async def case_resolver(input: CaseInput):
    """Main endpoint to resolve a Dynamics 365 case."""
    print(f"Received request to resolve case: {input.case_id}")

    initial_state = {
        "case_id": input.case_id,
        "task": input.task,
        "messages": [],
        "case_data": None,
        "resolution": None,
        "escalate": False,
        "next_action": "",
        "tool_result": None
    }

    try:
        result = app_graph.invoke(initial_state)

        # Extract message contents
        message_contents = []
        for msg in result["messages"]:
            if hasattr(msg, 'content'):
                message_contents.append(msg.content)
            elif isinstance(msg, dict) and 'content' in msg:
                message_contents.append(msg['content'])

        response = {
            "case_id": result["case_id"],
            "escalated": result.get("escalate", False),
            "resolution": result["resolution"].model_dump() if result.get("resolution") else None,
            # Changed from .dict() to .model_dump()
            "tool_result": result.get("tool_result"),
            "next_action": result.get("next_action"),
            "messages": message_contents,
            "success": True,
            "timestamp": datetime.now().isoformat()
        }

        print(f"Successfully processed case {input.case_id}")

        result = create_task_for_case(
            case_id=result["case_id"],
            task_subject="Task For Agent - Customer Request - Agentic Suggestions",
            task_description= str(result.get("resolution")) + '-' + str(message_contents) + ' - ' + str(result.get("tool_result")) + ' - ' + str(result.get("next_action")),
            due_date=datetime.now() + timedelta(days=5),
            priority=1
        )

        print(f"Result: {result}")


        return response

    except Exception as e:
        print(f"Error processing case {input.case_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(e),
                "case_id": input.case_id,
                "escalated": True,
                "success": False,
                "timestamp": datetime.now().isoformat()
            }
        )


@api.get("/health")
async def health_check():
    """Health check endpoint."""
    # Test LLM connectivity
    llm_healthy = False
    try:
        # Simple test to check if LLM is responding
        test_response = llm.invoke("Hello")
        llm_healthy = test_response is not None
    except:
        llm_healthy = False

    # Test Dynamics connectivity
    dynamics_healthy = token is not None

    status = "healthy" if llm_healthy and dynamics_healthy else "degraded"

    return {
        "status": status,
        "service": "Dynamics 365 Case Resolution Agent",
        "timestamp": datetime.now().isoformat(),
        "components": {
            "llm": {
                "available": llm_healthy,
                "model": "llama3.1:70b"
            },
            "dynamics": {
                "available": dynamics_healthy,
                "url": DYNAMICS_URL,
                "token_valid": token is not None
            }
        }
    }


@api.get("/test_fetch/{case_id}")
async def test_fetch(case_id: str):
    """Test endpoint to verify fetch_case_data works independently."""
    print(f"Testing fetch for case: {case_id}")
    try:
        result = fetch_case_data.invoke({"case_id": case_id})
        return {
            "success": True,
            "case_id": case_id,
            "case_data": result,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        print(f"Test fetch failed for case {case_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "case_id": case_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
        )


def main():
    """Main function to run the FastAPI server."""
    print("=" * 60)
    print("Dynamics 365 Case Resolution Agent")
    print("=" * 60)
    print(f"Dynamics URL: {DYNAMICS_URL}")
    print(f"LLM Model: llama3.1:8b")
    print(f"Server starting on: http://localhost:8001")
    print(f"Test endpoint: http://localhost:8001/test")
    print("CORS enabled: Yes (all origins)")
    print("Test mode: Enabled (using mock data)")
    print("=" * 60)

    # Run uvicorn
    uvicorn.run(
        api,
        host="127.0.0.1",
        port=8001,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
