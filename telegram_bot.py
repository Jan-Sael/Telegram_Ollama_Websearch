
import json
import time
import html
import re
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = "PASTE_HERE_TELEGRAM_BOT_TOKEN"

ALLOWED_TELEGRAM_USER_ID = #PASTE_HERE_YOUR_TELEGRAM_USER_ID

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "MODEL_NAME"  # e.g., "llama2-13b-chat"

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 4
WEB_SEARCH_RESULTS = 5

TELEGRAM_POLL_TIMEOUT = 30
HTTP_TIMEOUT = 300


# ============================================================
# GLOBAL STATE
# ============================================================

conversation_history = defaultdict(list)


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are a capable AI assistant running locally through Ollama.

You have access to one external tool:

web_search(query, max_results)

Use web_search when:
- the user asks for current information
- the user asks for latest or recent information
- the user asks about today's events
- the user asks about news
- facts may have changed since model training
- the user explicitly asks you to search the web
- the user asks you to verify something online
- current prices, versions, events, politics, markets,
  companies, products, or public developments matter

Do not use web_search when:
- answering timeless general knowledge
- writing or rewriting text
- translating
- brainstorming
- answering from information already supplied by the user
- the task does not require current information

When using web search:
- use the returned search results as evidence
- do not invent sources
- do not invent URLs
- mention uncertainty when results are insufficient
- include useful source URLs in the final answer when relevant

Be concise by default.
For technical questions, provide precise technical detail.
"""


# ============================================================
# OLLAMA TOOL DEFINITION
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public internet for current, recent, "
                "or externally verifiable information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Precise internet search query"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results from 1 to 10"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ============================================================
# BASIC HTTP HELPERS
# ============================================================

def http_get(url, timeout=30, headers=None):
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0 Safari/537.36"
        )
    }

    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(
        url,
        headers=request_headers,
        method="GET"
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:
        return response.read().decode(
            "utf-8",
            errors="replace"
        )


def http_post_json(url, payload, timeout=300):
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Python-Ollama-Telegram-Bot/1.0"
        },
        method="POST"
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout
    ) as response:
        body = response.read().decode(
            "utf-8",
            errors="replace"
        )

        return json.loads(body)


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api_url(method):
    return (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )


def telegram_request(method, payload=None, timeout=60):
    if payload is None:
        payload = {}

    return http_post_json(
        telegram_api_url(method),
        payload,
        timeout=timeout
    )


def send_message(chat_id, text):
    chunks = split_message(text)

    for chunk in chunks:
        try:
            telegram_request(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True
                },
                timeout=60
            )

        except Exception as exc:
            print(f"[TELEGRAM SEND ERROR] {exc}")


def send_typing(chat_id):
    try:
        telegram_request(
            "sendChatAction",
            {
                "chat_id": chat_id,
                "action": "typing"
            },
            timeout=20
        )

    except Exception:
        pass


def get_updates(offset=None):
    payload = {
        "timeout": TELEGRAM_POLL_TIMEOUT,
        "allowed_updates": ["message"]
    }

    if offset is not None:
        payload["offset"] = offset

    return telegram_request(
        "getUpdates",
        payload,
        timeout=TELEGRAM_POLL_TIMEOUT + 10
    )


# ============================================================
# TELEGRAM MESSAGE SPLITTING
# ============================================================

def split_message(text, max_length=4000):
    if not text:
        return ["Empty response from model."]

    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        split_at = remaining.rfind(
            "\n",
            0,
            max_length
        )

        if split_at < max_length // 2:
            split_at = remaining.rfind(
                " ",
                0,
                max_length
            )

        if split_at <= 0:
            split_at = max_length

        chunk = remaining[:split_at].strip()

        if chunk:
            chunks.append(chunk)

        remaining = remaining[split_at:].strip()

    return chunks


# ============================================================
# HTML CLEANING
# ============================================================

def strip_html_tags(text):
    text = re.sub(
        r"<script.*?</script>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    text = re.sub(
        r"<style.*?</style>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE
    )

    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def decode_duckduckgo_url(url):
    url = html.unescape(url)

    if url.startswith("//"):
        url = "https:" + url

    parsed = urllib.parse.urlparse(url)

    query = urllib.parse.parse_qs(
        parsed.query
    )

    if "uddg" in query:
        return urllib.parse.unquote(
            query["uddg"][0]
        )

    return url


# ============================================================
# FREE WEB SEARCH
# ============================================================

def web_search(query, max_results=5):
    """
    Free web search using DuckDuckGo's HTML endpoint.

    No API key.
    No pip package.
    Standard library only.
    """

    max_results = max(
        1,
        min(int(max_results), 10)
    )

    print(f"[WEB SEARCH] {query}")

    encoded_query = urllib.parse.urlencode({
        "q": query
    })

    url = (
        "https://html.duckduckgo.com/html/?"
        + encoded_query
    )

    try:
        page = http_get(
            url,
            timeout=30,
            headers={
                "Accept-Language": "en-US,en;q=0.9"
            }
        )

        results = []

        # Match DuckDuckGo result links
        link_pattern = re.compile(
            r'<a[^>]+class="[^"]*result__a[^"]*"'
            r'[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE
        )

        matches = list(
            link_pattern.finditer(page)
        )

        for index, match in enumerate(matches):
            if len(results) >= max_results:
                break

            raw_url = match.group(1)
            raw_title = match.group(2)

            title = strip_html_tags(raw_title)
            result_url = decode_duckduckgo_url(
                raw_url
            )

            # Try to locate nearby snippet
            start = match.end()

            if index + 1 < len(matches):
                end = matches[index + 1].start()
            else:
                end = min(
                    len(page),
                    start + 5000
                )

            nearby_html = page[start:end]

            snippet_match = re.search(
                r'class="[^"]*result__snippet[^"]*"'
                r'[^>]*>(.*?)</(?:a|div)>',
                nearby_html,
                re.DOTALL | re.IGNORECASE
            )

            snippet = ""

            if snippet_match:
                snippet = strip_html_tags(
                    snippet_match.group(1)
                )

            if title and result_url:
                results.append({
                    "title": title,
                    "url": result_url,
                    "snippet": snippet
                })

        return json.dumps(
            {
                "query": query,
                "result_count": len(results),
                "results": results
            },
            ensure_ascii=False,
            indent=2
        )

    except Exception as exc:
        print(f"[WEB SEARCH ERROR] {exc}")

        return json.dumps(
            {
                "query": query,
                "error": str(exc),
                "results": []
            },
            ensure_ascii=False
        )


# ============================================================
# TOOL EXECUTION
# ============================================================

def execute_tool(name, arguments):
    print(
        f"[TOOL CALL] "
        f"name={name} "
        f"arguments={arguments}"
    )

    if name == "web_search":
        query = str(
            arguments.get("query", "")
        ).strip()

        if not query:
            return json.dumps({
                "error": (
                    "web_search requires "
                    "a non-empty query"
                )
            })

        max_results = arguments.get(
            "max_results",
            WEB_SEARCH_RESULTS
        )

        try:
            max_results = int(max_results)

        except (TypeError, ValueError):
            max_results = WEB_SEARCH_RESULTS

        return web_search(
            query,
            max_results
        )

    return json.dumps({
        "error": f"Unknown tool: {name}"
    })


# ============================================================
# OLLAMA API
# ============================================================

def ollama_chat(messages, include_tools=True):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.3
        }
    }

    if include_tools:
        payload["tools"] = TOOLS

    return http_post_json(
        f"{OLLAMA_URL}/api/chat",
        payload,
        timeout=HTTP_TIMEOUT
    )


def check_ollama():
    try:
        url = f"{OLLAMA_URL}/api/tags"

        body = http_get(
            url,
            timeout=10
        )

        data = json.loads(body)

        model_names = [
            model.get("name", "")
            for model in data.get("models", [])
        ]

        return True, model_names

    except Exception as exc:
        return False, str(exc)


# ============================================================
# AGENT LOOP
# ============================================================

def run_agent(chat_id, user_text):
    history = conversation_history[chat_id]

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    messages.extend(
        history[-MAX_HISTORY_MESSAGES:]
    )

    user_message = {
        "role": "user",
        "content": user_text
    }

    messages.append(user_message)

    for round_number in range(MAX_TOOL_ROUNDS):
        print(
            f"[AGENT ROUND] "
            f"{round_number + 1}/"
            f"{MAX_TOOL_ROUNDS}"
        )

        response = ollama_chat(
            messages,
            include_tools=True
        )

        assistant_message = response.get(
            "message",
            {}
        )

        tool_calls = assistant_message.get(
            "tool_calls"
        ) or []

        # ----------------------------------------
        # FINAL RESPONSE
        # ----------------------------------------

        if not tool_calls:
            final_text = str(
                assistant_message.get(
                    "content",
                    ""
                )
            ).strip()

            if not final_text:
                final_text = (
                    "The model returned "
                    "an empty response."
                )

            history.append(user_message)

            history.append({
                "role": "assistant",
                "content": final_text
            })

            conversation_history[chat_id] = (
                history[-MAX_HISTORY_MESSAGES:]
            )

            return final_text

        # ----------------------------------------
        # MODEL REQUESTED TOOLS
        # ----------------------------------------

        messages.append(
            assistant_message
        )

        for tool_call in tool_calls:
            function_data = tool_call.get(
                "function",
                {}
            )

            tool_name = function_data.get(
                "name",
                ""
            )

            arguments = function_data.get(
                "arguments",
                {}
            )

            # Some models return arguments as JSON text
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(
                        arguments
                    )

                except json.JSONDecodeError:
                    arguments = {}

            if not isinstance(arguments, dict):
                arguments = {}

            tool_result = execute_tool(
                tool_name,
                arguments
            )

            messages.append({
                "role": "tool",
                "tool_name": tool_name,
                "content": tool_result
            })

    # ========================================================
    # TOOL LOOP LIMIT REACHED
    # ========================================================

    messages.append({
        "role": "system",
        "content": (
            "The tool-call limit has been reached. "
            "Provide the best final answer now using "
            "the information already available. "
            "Do not call any more tools."
        )
    })

    response = ollama_chat(
        messages,
        include_tools=False
    )

    final_text = str(
        response.get(
            "message",
            {}
        ).get(
            "content",
            ""
        )
    ).strip()

    if not final_text:
        final_text = (
            "Could not produce a final response."
        )

    history.append(user_message)

    history.append({
        "role": "assistant",
        "content": final_text
    })

    conversation_history[chat_id] = (
        history[-MAX_HISTORY_MESSAGES:]
    )

    return final_text


# ============================================================
# COMMAND HANDLERS
# ============================================================

def handle_start(chat_id):
    send_message(
        chat_id,
        (
            "Connected.\n\n"
            f"Model: {OLLAMA_MODEL}\n"
            "Web search: enabled\n"
            "Python dependencies: none\n"
            "Access: restricted\n\n"
            "Send me a message."
        )
    )


def handle_help(chat_id):
    send_message(
        chat_id,
        (
            "Commands:\n\n"
            "/start - Start bot\n"
            "/status - Check Ollama\n"
            "/clear - Clear conversation memory\n"
            "/help - Show commands\n\n"
            "The model can automatically use "
            "web search when current information "
            "is required."
        )
    )


def handle_clear(chat_id):
    conversation_history.pop(
        chat_id,
        None
    )

    send_message(
        chat_id,
        "Conversation memory cleared."
    )


def handle_status(chat_id):
    online, result = check_ollama()

    if online:
        model_found = (
            OLLAMA_MODEL in result
        )

        send_message(
            chat_id,
            (
                "Status: online\n"
                f"Ollama: {OLLAMA_URL}\n"
                f"Model: {OLLAMA_MODEL}\n"
                f"Model installed: {model_found}\n"
                "Web search: enabled"
            )
        )

    else:
        send_message(
            chat_id,
            (
                "Status: Ollama unreachable\n"
                f"Endpoint: {OLLAMA_URL}\n"
                f"Error: {result}"
            )
        )


# ============================================================
# MESSAGE HANDLER
# ============================================================

def handle_message(message):
    user = message.get("from", {})
    chat = message.get("chat", {})

    user_id = user.get("id")
    chat_id = chat.get("id")
    text = message.get("text", "")

    if not chat_id:
        return

    # --------------------------------------------------------
    # SECURITY: ONLY ALLOWED USER
    # --------------------------------------------------------

    if user_id != ALLOWED_TELEGRAM_USER_ID:
        print(
            f"[UNAUTHORIZED] "
            f"user_id={user_id} "
            f"username={user.get('username')}"
        )

        send_message(
            chat_id,
            "Unauthorized."
        )

        return

    if not text:
        send_message(
            chat_id,
            "Currently only text messages are supported."
        )

        return

    print(
        f"[MESSAGE] "
        f"user_id={user_id} "
        f"text={text[:200]}"
    )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    command = text.strip().lower()

    if command == "/start":
        handle_start(chat_id)
        return

    if command == "/help":
        handle_help(chat_id)
        return

    if command == "/clear":
        handle_clear(chat_id)
        return

    if command == "/status":
        handle_status(chat_id)
        return

    # --------------------------------------------------------
    # NORMAL AI MESSAGE
    # --------------------------------------------------------

    send_typing(chat_id)

    try:
        answer = run_agent(
            chat_id,
            text
        )

        send_message(
            chat_id,
            answer
        )

    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode(
                "utf-8",
                errors="replace"
            )

        except Exception:
            error_body = str(exc)

        print(
            f"[HTTP ERROR] "
            f"{exc.code}: {error_body}"
        )

        send_message(
            chat_id,
            (
                f"HTTP error {exc.code}\n\n"
                f"{error_body[:2000]}"
            )
        )

    except urllib.error.URLError as exc:
        print(
            f"[CONNECTION ERROR] {exc}"
        )

        send_message(
            chat_id,
            (
                "Connection error.\n\n"
                f"Could not reach: {OLLAMA_URL}\n"
                f"Error: {exc}"
            )
        )

    except Exception as exc:
        print(
            f"[ERROR] "
            f"{type(exc).__name__}: {exc}"
        )

        send_message(
            chat_id,
            (
                f"Error: "
                f"{type(exc).__name__}: {exc}"
            )
        )


# ============================================================
# MAIN POLLING LOOP
# ============================================================

def main():
    if (
        not TELEGRAM_BOT_TOKEN
        or "PUT_YOUR_NEW" in TELEGRAM_BOT_TOKEN
    ):
        print(
            "\nERROR:\n"
            "Set TELEGRAM_BOT_TOKEN at the top "
            "of main.py first.\n"
        )

        return

    print("=" * 60)
    print("Telegram + Ollama Agent")
    print("=" * 60)
    print(f"Model:       {OLLAMA_MODEL}")
    print(f"Ollama URL:  {OLLAMA_URL}")
    print(f"Allowed ID:  {ALLOWED_TELEGRAM_USER_ID}")
    print("Web search:  enabled")
    print("Dependencies: Python standard library only")
    print("=" * 60)

    # --------------------------------------------------------
    # CHECK OLLAMA AT STARTUP
    # --------------------------------------------------------

    print("\nChecking Ollama...")

    online, result = check_ollama()

    if online:
        print("[OK] Ollama reachable")

        if OLLAMA_MODEL in result:
            print(
                f"[OK] Model found: "
                f"{OLLAMA_MODEL}"
            )

        else:
            print(
                f"[WARNING] Model not found: "
                f"{OLLAMA_MODEL}"
            )

            print(
                "[INFO] Available models:"
            )

            for model in result:
                print(f"  - {model}")

    else:
        print(
            f"[WARNING] Ollama unreachable: "
            f"{result}"
        )

    # --------------------------------------------------------
    # TEST TELEGRAM
    # --------------------------------------------------------

    print("\nChecking Telegram bot token...")

    try:
        bot_info = telegram_request(
            "getMe",
            {},
            timeout=20
        )

        if bot_info.get("ok"):
            bot = bot_info.get(
                "result",
                {}
            )

            print(
                f"[OK] Telegram bot: "
                f"@{bot.get('username')}"
            )

        else:
            print(
                "[ERROR] Telegram token rejected"
            )

            return

    except Exception as exc:
        print(
            f"[ERROR] Telegram connection failed: "
            f"{exc}"
        )

        return

    # --------------------------------------------------------
    # POLLING
    # --------------------------------------------------------

    print("\nBot is running.")
    print("Press Ctrl+C to stop.\n")

    offset = None

    while True:
        try:
            updates = get_updates(
                offset
            )

            if not updates.get("ok"):
                print(
                    f"[TELEGRAM ERROR] "
                    f"{updates}"
                )

                time.sleep(3)
                continue

            for update in updates.get(
                "result",
                []
            ):
                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:
                    offset = update_id + 1

                message = update.get(
                    "message"
                )

                if message:
                    handle_message(message)

        except KeyboardInterrupt:
            print("\nBot stopped.")
            break

        except urllib.error.URLError as exc:
            print(
                f"[NETWORK ERROR] {exc}"
            )

            time.sleep(5)

        except Exception as exc:
            print(
                f"[POLLING ERROR] "
                f"{type(exc).__name__}: {exc}"
            )

            time.sleep(5)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()

