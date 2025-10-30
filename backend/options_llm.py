import os
import base64
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com")
CHAT_API = f"{API_BASE}/v1/chat/completions"
DEBUG_OPTIONS = os.getenv("DEBUG_OPTIONS", "0") == "1"

logger = logging.getLogger("options_llm")
if DEBUG_OPTIONS:
    logging.basicConfig(level=logging.INFO)


def _auth_headers():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    return {"Authorization": f"Bearer {api_key}"}


def _dbg(msg: str, details: dict | None = None):
    if not DEBUG_OPTIONS:
        return
    try:
        if details is None:
            logger.info(f"[options_llm] {msg}")
        else:
            # Keep logs compact and safe
            compact = {}
            for k, v in details.items():
                if isinstance(v, (str, int, float, bool)) or v is None:
                    compact[k] = v
                elif isinstance(v, (list, tuple)):
                    compact[k] = f"list[{len(v)}]"
                elif isinstance(v, dict):
                    compact[k] = {"keys": list(v.keys())[:10]}
                else:
                    compact[k] = type(v).__name__
            logger.info(f"[options_llm] {msg} :: {compact}")
    except Exception:
        # Never let logging break runtime
        pass


def propose_options(summary: str, last_frame_path: str, n: int = 3):
    # Default options in case the LLM call fails (max 5 words each)
    fallback = [
        {"label": "Push forward", "sora_prompt": "Continue the scene with a forward movement. Dialogue: - \"Keep going!\""},
        {"label": "Duck into cover", "sora_prompt": "The character moves into cover and assesses the street. Dialogue: - \"Hold on...\""},
        {"label": "Change direction", "sora_prompt": "The character pivots and takes a side route. Dialogue: - \"This way!\""},
    ][:n]

    try:
        b64 = None
        if last_frame_path and os.path.exists(last_frame_path):
            with open(last_frame_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

        prompt = f"""
You are a narrative designer. Based on the running summary below and the current frame,
propose {n} distinct next actions. Output MUST be a single JSON object of the form:
{{
    "options": [
        {{ "label": "...", "sora_prompt": "..." }},
        ...
    ]
}}
Each option must include:
- "label": maximum 5 words (keep it short and punchy),
- "sora_prompt": 1–2 sentences describing the next beat (visual), plus a Dialogue: block if any.

Keep them visually actionable, not abstract. Maintain continuity and avoid introducing new major characters unless already foreshadowed.

SUMMARY:
{summary}
"""

        # Build content parts for chat API
        content_parts = [{"type": "text", "text": prompt}]
        if b64:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        body = {
            "model": os.getenv("OPTIONS_MODEL", "gpt-5-mini"),
            "messages": [
                {
                    "role": "user",
                    "content": content_parts,
                }
            ],
            "max_completion_tokens": 2000,  # Increased to allow for reasoning tokens + output
            "reasoning_effort": "low",  # Reduce reasoning tokens to prioritize output generation
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "options_schema",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "options": {
                                "type": "array",
                                "description": "List of exactly 3 action options",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": "Maximum 5 words describing the action"
                                        },
                                        "sora_prompt": {
                                            "type": "string",
                                            "description": "1-2 sentences with visual description and dialogue"
                                        }
                                    },
                                    "required": ["label", "sora_prompt"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["options"],
                        "additionalProperties": False
                    }
                }
            },
        }

        r = requests.post(
            CHAT_API,
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
        _dbg("chat completions first attempt", {"status": r.status_code})
        # If the API returns a 4xx/5xx, try a text-only attempt before falling back
        if r.status_code >= 400:
            _dbg("first attempt failed", {"status": r.status_code, "text": r.text[:280]})
            body_text_only = {
                "model": os.getenv("OPTIONS_MODEL", "gpt-5-mini"),
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "max_completion_tokens": 2000,  # Increased to allow for reasoning tokens + output
                "reasoning_effort": "low",  # Reduce reasoning tokens to prioritize output generation
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "options_schema",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "options": {
                                    "type": "array",
                                    "description": "List of exactly 3 action options",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": "Maximum 5 words describing the action"
                                            },
                                            "sora_prompt": {
                                                "type": "string",
                                                "description": "1-2 sentences with visual description and dialogue"
                                            }
                                        },
                                        "required": ["label", "sora_prompt"],
                                        "additionalProperties": False
                                    }
                                }
                            },
                            "required": ["options"],
                            "additionalProperties": False
                        }
                    }
                },
            }
            r = requests.post(
                CHAT_API,
                headers={**_auth_headers(), "Content-Type": "application/json"},
                json=body_text_only,
                timeout=90,
            )
            _dbg("chat completions second attempt (text-only)", {"status": r.status_code})
            if r.status_code >= 400:
                _dbg("text-only attempt failed", {"status": r.status_code, "text": r.text[:280]})
                return fallback, True

        # Log raw response for debugging
        _dbg("raw response text", {"text": r.text[:1000]})
        
        data = r.json()
        _dbg("response top-level", {"keys": list(data.keys())})

        # Parse Chat Completions API response format
        parsed = None
        json_text = None
        try:
            # Standard chat completions format
            choices = data.get("choices", [])
            _dbg("choices info", {"has_choices": bool(choices), "count": len(choices) if choices else 0})
            if choices and isinstance(choices, list):
                first_choice = choices[0]
                _dbg("first_choice keys", {"keys": list(first_choice.keys()) if isinstance(first_choice, dict) else "not_dict"})
                message = first_choice.get("message", {})
                _dbg("message keys", {"keys": list(message.keys()) if isinstance(message, dict) else "not_dict"})
                
                # With GPT-5-mini structured outputs, check annotations field first
                annotations = message.get("annotations", [])
                _dbg("annotations", {"count": len(annotations) if annotations else 0, "type": type(annotations).__name__})
                
                if annotations and isinstance(annotations, list) and len(annotations) > 0:
                    # Structured output is in annotations
                    first_annotation = annotations[0]
                    _dbg("first annotation", {"type": type(first_annotation).__name__, "preview": str(first_annotation)[:200]})
                    if isinstance(first_annotation, dict):
                        parsed = first_annotation
                    elif isinstance(first_annotation, str):
                        json_text = first_annotation
                
                # Fallback to content field if annotations empty
                if not parsed and not json_text:
                    content = message.get("content", "")
                    _dbg("content fallback", {"type": type(content).__name__, "length": len(str(content)) if content else 0})
                    if content:
                        json_text = content
        except Exception as e:
            _dbg("parsing exception", {"error": str(e)[:200]})

        if parsed is None and json_text is None:
            # Fallback to any text we can find
            _dbg("no content found in response", {"data_keys": list(data.keys())})
            return fallback, True
            
        _dbg("after parse probing", {"has_parsed": isinstance(parsed, (dict, list)), "has_text": bool(json_text)})

        # Normalize to a dict/list
        options_list = []
        try:
            if parsed is None:
                parsed = json.loads(json_text or "{}")
        except Exception:
            # Last chance: if text isn't valid JSON, use fallback
            _dbg("json.loads failed; falling back", {"snippet": (json_text or "")[:160]})
            return fallback, True

        try:
            raw_options = parsed.get("options") if isinstance(parsed, dict) else parsed
            if isinstance(raw_options, list):
                for opt in raw_options:
                    if not isinstance(opt, dict):
                        continue
                    label = str(opt.get("label", "Option")).strip()[:60]
                    prompt_val = str(opt.get("sora_prompt", "Continue the scene.")).strip()
                    if label and prompt_val:
                        options_list.append({"label": label, "sora_prompt": prompt_val})
            else:
                _dbg("parsed without options list", {"type": type(parsed).__name__})
        except Exception:
            _dbg("normalization error", {})
            return fallback, True

        if options_list:
            return options_list, False
        else:
            _dbg("no options extracted; using fallback", {})
            return fallback, True
    except Exception as e:
        _dbg("propose_options exception", {"error": str(e)[:200]})
        return fallback, True
