from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Any, Optional
import requests
import anthropic
import json
import os
from datetime import date

app = FastAPI()

# ----------------------------------------------------------------
# Config from environment variables
# ----------------------------------------------------------------
NOTION_TOKEN      = os.environ.get("NOTION_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PARENT_PAGE_ID    = "3492bc72289b8081970ac57e2816e0c5"
FIXED_DATABASE_ID = "3492bc72289b80dda791c15cf5a575e4"

# ----------------------------------------------------------------
# In-memory Revit data store
# ----------------------------------------------------------------
_revit_data = []

class UploadPayload(BaseModel):
    elements: List[Any]


def format_uuid(uid):
    uid = uid.replace("-", "")
    return f"{uid[0:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:32]}"


def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }


# ----------------------------------------------------------------
# Basic endpoints
# ----------------------------------------------------------------
@app.get("/")
def home():
    return {"status": "Revit Automation Server running"}


@app.post("/upload-data")
def upload_data(payload: UploadPayload):
    global _revit_data
    _revit_data = payload.elements
    summary = {}
    for el in _revit_data:
        cat = el.get("category", "Unknown")
        summary[cat] = summary.get(cat, 0) + 1
    return {"status": "success", "summary": summary}


@app.get("/get-data")
def get_data(category: str = None, keyword: str = None):
    data = _revit_data
    if category and category.lower() != "all":
        data = [e for e in data if e.get("category", "").lower() == category.lower()]
    if keyword:
        kw = keyword.lower()
        data = [e for e in data if kw in e.get("name", "").lower()]
    return {"status": "success", "elements": data, "count": len(data)}


@app.get("/get-summary")
def get_summary():
    summary = {}
    for el in _revit_data:
        cat = el.get("category", "Unknown")
        summary[cat] = summary.get(cat, 0) + 1
    return {"summary": summary, "total": len(_revit_data)}


# ----------------------------------------------------------------
# Notion schema and helpers
# ----------------------------------------------------------------
SCHEMA = {
    "Name":                   {"title": {}},
    "Category":               {"rich_text": {}},
    "UniqueId":               {"rich_text": {}},
    "Element ID":             {"number": {"format": "number"}},
    "Level":                  {"rich_text": {}},
    "Number":                 {"rich_text": {}},
    "Comments":               {"rich_text": {}},
    "Phase Created":          {"rich_text": {}},
    "Phase Demolished":       {"rich_text": {}},
    "Area (m2)":              {"number": {"format": "number"}},
    "Perimeter (m)":          {"number": {"format": "number"}},
    "Height (m)":             {"number": {"format": "number"}},
    "Department":             {"rich_text": {}},
    "Occupancy":              {"rich_text": {}},
    "Width (m)":              {"number": {"format": "number"}},
    "Fire Rating":            {"rich_text": {}},
    "Frame Material":         {"rich_text": {}},
    "Length (m)":             {"number": {"format": "number"}},
    "Volume (m3)":            {"number": {"format": "number"}},
    "Base Constraint":        {"rich_text": {}},
    "Top Constraint":         {"rich_text": {}},
    "Unconnected Height (m)": {"number": {"format": "number"}},
    "Function":               {"rich_text": {}},
    "Thickness (m)":          {"number": {"format": "number"}},
    "Structural":             {"number": {"format": "number"}},
    "Version":                {"number": {"format": "number"}},
    "Export Date":            {"date": {}},
}


def build_props(el, version=None, export_date=None):
    cat = el.get("category", "")

    def rt(val):
        return {"rich_text": [{"text": {"content": str(val) if val else ""}}]}

    def num(val):
        return {"number": val if val is not None else 0}

    props = {
        "Name":             {"title": [{"text": {"content": el.get("name", "")}}]},
        "Category":         rt(cat),
        "UniqueId":         rt(el.get("unique_id", "")),
        "Element ID":       num(el.get("element_id", 0)),
        "Level":            rt(el.get("level", "")),
        "Number":           rt(el.get("number", "")),
        "Comments":         rt(el.get("comments", "")),
        "Phase Created":    rt(el.get("phase_created", "")),
        "Phase Demolished": rt(el.get("phase_demolished", "")),
    }

    if cat == "Room":
        props["Area (m2)"]     = num(el.get("area", 0))
        props["Perimeter (m)"] = num(el.get("perimeter", 0))
        props["Height (m)"]    = num(el.get("height", 0))
        props["Department"]    = rt(el.get("department", ""))
        props["Occupancy"]     = rt(el.get("occupancy", ""))
    elif cat == "Door":
        props["Width (m)"]      = num(el.get("width", 0))
        props["Height (m)"]     = num(el.get("height", 0))
        props["Fire Rating"]    = rt(el.get("fire_rating", ""))
        props["Frame Material"] = rt(el.get("frame_material", ""))
    elif cat == "Wall":
        props["Length (m)"]             = num(el.get("length", 0))
        props["Area (m2)"]              = num(el.get("area", 0))
        props["Volume (m3)"]            = num(el.get("volume", 0))
        props["Base Constraint"]        = rt(el.get("base_constraint", ""))
        props["Top Constraint"]         = rt(el.get("top_constraint", ""))
        props["Unconnected Height (m)"] = num(el.get("unconnected_height", 0))
        props["Function"]               = rt(el.get("function", ""))
    elif cat == "Floor":
        props["Area (m2)"]     = num(el.get("area", 0))
        props["Volume (m3)"]   = num(el.get("volume", 0))
        props["Thickness (m)"] = num(el.get("thickness", 0))
        props["Structural"]    = num(el.get("structural", 0))

    if version is not None:
        props["Version"] = num(version)
    if export_date:
        props["Export Date"] = {"date": {"start": export_date}}

    return props


def notion_create_database(title):
    """Create a new Notion database and return its ID."""
    body = {
        "parent": {"type": "page_id", "page_id": format_uuid(PARENT_PAGE_ID)},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": SCHEMA
    }
    res  = requests.post("https://api.notion.com/v1/databases", headers=notion_headers(), json=body)
    data = res.json()
    if "id" in data:
        return format_uuid(data["id"]), None
    return None, str(data)


def notion_push_elements(db_id, elements, version=None, export_date=None):
    """Push elements to a Notion database. Returns (ok_count, fail_count)."""
    ok, fail = 0, 0
    for el in elements:
        props = build_props(el, version=version, export_date=export_date)
        body  = {"parent": {"database_id": db_id}, "properties": props}
        res   = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=body)
        if res.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"FAILED {el.get('name')}: {res.text}")
    return ok, fail


def notion_get_next_version(db_id):
    """Get the next version number for a fixed database."""
    res  = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=notion_headers(), json={}
    )
    versions = []
    for page in res.json().get("results", []):
        v = page["properties"].get("Version", {}).get("number")
        if v is not None:
            versions.append(v)
    return max(versions) + 1 if versions else 1


# ----------------------------------------------------------------
# Claude Agent tools
# ----------------------------------------------------------------
TOOLS = [
    {
        "name": "get_revit_summary",
        "description": "Get a count of all Revit elements by category. Call this first.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "preview_filtered_data",
        "description": "Preview how many elements match a category and/or keyword before pushing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["Room", "Door", "Wall", "Floor", "Parking"]
                },
                "keyword": {"type": "string"}
            },
            "required": []
        }
    },
    {
        "name": "create_new_database",
        "description": "Create a new Notion database and push filtered Revit elements into it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":    {"type": "string"},
                "category": {"type": "string", "enum": ["Room", "Door", "Wall", "Floor", "Parking"]},
                "keyword":  {"type": "string"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "push_versioned",
        "description": "Push elements into the existing fixed Notion database with a version number.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["Room", "Door", "Wall", "Floor", "Parking"]},
                "keyword":  {"type": "string"}
            },
            "required": []
        }
    }
]

SYSTEM_PROMPT = """You are a helpful BIM assistant for an architectural firm.
You have access to Revit model data (Rooms, Doors, Walls, Floors, Parking) uploaded via PyRevit.

When the user asks to export data to Notion:
1. Call get_revit_summary to see what's available and tell the user the counts.
2. If filtering by keyword or category, call preview_filtered_data first to confirm the count.
3. Ask: "Would you like to create a new Notion database, or add this as a new version to the existing database?"
4. Call create_new_database or push_versioned with title, category, and/or keyword.

Never call create_new_database or push_versioned more than once per user request.
Keep responses concise and friendly."""


def get_filtered_elements(category=None, keyword=None):
    data = _revit_data
    if category:
        data = [e for e in data if e.get("category", "").lower() == category.lower()]
    if keyword:
        kw = keyword.lower()
        data = [e for e in data if kw in e.get("name", "").lower()]
    return data


def run_tool(name, input_data):
    if name == "get_revit_summary":
        summary = {}
        for el in _revit_data:
            cat = el.get("category", "Unknown")
            summary[cat] = summary.get(cat, 0) + 1
        return {"summary": summary, "total": len(_revit_data)}

    elif name == "preview_filtered_data":
        elements = get_filtered_elements(input_data.get("category"), input_data.get("keyword"))
        sample   = [e.get("name", "") for e in elements[:5]]
        return {"count": len(elements), "sample_names": sample}

    elif name == "create_new_database":
        title    = input_data["title"]
        elements = get_filtered_elements(input_data.get("category"), input_data.get("keyword"))
        if not elements:
            return {"error": "No elements found"}

        db_id, err = notion_create_database(title)
        if err:
            return {"error": "Failed to create database", "detail": err}

        ok, fail = notion_push_elements(db_id, elements, export_date=str(date.today()))
        return {"status": "success", "title": title, "pushed": ok, "failed": fail}

    elif name == "push_versioned":
        elements = get_filtered_elements(input_data.get("category"), input_data.get("keyword"))
        if not elements:
            return {"error": "No elements found"}

        db_id   = format_uuid(FIXED_DATABASE_ID)
        version = notion_get_next_version(db_id)
        ok, fail = notion_push_elements(db_id, elements, version=version, export_date=str(date.today()))
        return {"status": "success", "version": version, "pushed": ok, "failed": fail}

    return {"error": f"Unknown tool: {name}"}


# ----------------------------------------------------------------
# Chat endpoint
# ----------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = []


@app.post("/ask")
def ask(req: ChatRequest):
    client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = req.history + [{"role": "user", "content": req.message}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=TOOLS,
        )

        messages.append({"role": "assistant", "content": response.content})

        tool_uses  = [b for b in response.content if b.type == "tool_use"]
        text_parts = [b.text for b in response.content if b.type == "text"]

        if not tool_uses:
            return {"reply": " ".join(text_parts), "history": messages}

        tool_results = []
        for tool_block in tool_uses:
            result = run_tool(tool_block.name, tool_block.input)
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tool_block.id,
                "content":     json.dumps(result)
            })

        messages.append({"role": "user", "content": tool_results})


# ----------------------------------------------------------------
# Chat UI
# ----------------------------------------------------------------
@app.get("/chat", response_class=HTMLResponse)
def chat_ui():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Revit → Notion BIM Assistant</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f5f5f5;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        header {
            background: #1a1a2e;
            color: white;
            padding: 16px 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        header h1 { font-size: 18px; font-weight: 600; }
        #status-bar {
            background: #16213e;
            color: #aaa;
            font-size: 12px;
            padding: 6px 24px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        #status-dot { width: 8px; height: 8px; border-radius: 50%; background: #888; }
        #status-dot.online { background: #4caf50; }
        #chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        .message {
            max-width: 70%;
            padding: 12px 16px;
            border-radius: 12px;
            line-height: 1.5;
            font-size: 14px;
            white-space: pre-wrap;
        }
        .message.user {
            background: #1a1a2e;
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 4px;
        }
        .message.assistant {
            background: white;
            color: #333;
            align-self: flex-start;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .message.thinking {
            background: white;
            color: #999;
            align-self: flex-start;
            font-style: italic;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        #input-area {
            background: white;
            border-top: 1px solid #e0e0e0;
            padding: 16px 24px;
            display: flex;
            gap: 12px;
        }
        #user-input {
            flex: 1;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 10px 14px;
            font-size: 14px;
            outline: none;
            resize: none;
            height: 44px;
        }
        #user-input:focus { border-color: #1a1a2e; }
        #send-btn {
            background: #1a1a2e;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 0 20px;
            font-size: 14px;
            cursor: pointer;
            height: 44px;
        }
        #send-btn:hover { background: #16213e; }
        #send-btn:disabled { background: #999; cursor: not-allowed; }
        .welcome {
            text-align: center;
            color: #999;
            font-size: 14px;
            margin: auto;
            padding: 40px;
        }
        .welcome h2 { font-size: 20px; color: #555; margin-bottom: 8px; }
        .suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: center;
            margin-top: 16px;
        }
        .suggestion {
            background: white;
            border: 1px solid #ddd;
            border-radius: 20px;
            padding: 6px 14px;
            font-size: 13px;
            cursor: pointer;
            color: #555;
        }
        .suggestion:hover { border-color: #1a1a2e; color: #1a1a2e; }
    </style>
</head>
<body>
<header>
    <span>🏗️</span>
    <h1>Revit → Notion BIM Assistant</h1>
</header>
<div id="status-bar">
    <div id="status-dot"></div>
    <span id="status-text">Checking server...</span>
</div>
<div id="chat-container">
    <div class="welcome">
        <h2>Hi! I'm your BIM Assistant</h2>
        <p>I can help you export Revit model data to Notion.<br>
        Make sure you've clicked the PyRevit button first to upload your model data.</p>
        <div class="suggestions">
            <div class="suggestion" onclick="sendSuggestion(this)">What data is available?</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Export all doors to a new database</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Export FD1 doors only</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Push all rooms with version number</div>
            <div class="suggestion" onclick="sendSuggestion(this)">Export parking data</div>
        </div>
    </div>
</div>
<div id="input-area">
    <textarea id="user-input" placeholder="Ask me to export data to Notion..."></textarea>
    <button id="send-btn" onclick="sendMessage()">Send</button>
</div>
<script>
    let history = [];

    fetch('/get-summary')
        .then(r => r.json())
        .then(data => {
            const dot  = document.getElementById('status-dot');
            const text = document.getElementById('status-text');
            dot.classList.add('online');
            const total = data.total || 0;
            if (total > 0) {
                const parts = Object.entries(data.summary).map(([k,v]) => v + ' ' + k + 's');
                text.textContent = 'Model loaded: ' + parts.join(', ');
            } else {
                text.textContent = 'Server online — no model data yet. Click the PyRevit button first.';
            }
        })
        .catch(() => {
            document.getElementById('status-text').textContent = 'Server offline';
        });

    function sendSuggestion(el) {
        document.getElementById('user-input').value = el.textContent;
        sendMessage();
    }

    function addMessage(text, role) {
        const welcome = document.querySelector('.welcome');
        if (welcome) welcome.remove();
        const container = document.getElementById('chat-container');
        const div = document.createElement('div');
        div.className = 'message ' + role;
        div.textContent = text;
        container.appendChild(div);
        container.scrollTop = container.scrollHeight;
        return div;
    }

    async function sendMessage() {
        const input = document.getElementById('user-input');
        const btn   = document.getElementById('send-btn');
        const text  = input.value.trim();
        if (!text) return;
        input.value = '';
        btn.disabled = true;
        addMessage(text, 'user');
        const thinking = addMessage('Thinking...', 'thinking');
        try {
            const res = await fetch('/ask', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message: text, history: history})
            });
            const data = await res.json();
            thinking.remove();
            addMessage(data.reply, 'assistant');
            history = data.history;
        } catch (e) {
            thinking.remove();
            addMessage('Error: ' + e.message, 'assistant');
        }
        btn.disabled = false;
        input.focus();
    }

    document.getElementById('user-input').addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
</script>
</body>
</html>
"""
