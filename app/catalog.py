SCENARIOS = [
    {
        "id": "good",
        "title": "1. Fastest first token",
        "kind": "single",
        "summary": "Start the chat immediately. Do not wait for the database. This is the quickest first token — just the model.",
    },
    {
        "id": "nat",
        "title": "2. Wait for DB, then chat",
        "kind": "single",
        "summary": "Wait for the allow-list check, then start the chat. This process reuses one saved database connection, so you pay a full connect only once.",
    },
    {
        "id": "scale",
        "title": "3. Brand-new container",
        "kind": "single",
        "summary": "Like a new Cloud Run instance: no saved connection. Every request opens a new database connection, then chats. Slower than card 2.",
    },
    {
        "id": "timeout",
        "title": "4. Chat starts, then DB fails",
        "kind": "abort",
        "summary": "Chat begins while a database connection is attempted. That attempt never gets through (IP not allowed). Tokens stop. Compare first-token time with time until the chat is cut off.",
    },
]
