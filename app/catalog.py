SCENARIOS = [
    {
        "id": "good",
        "title": "1. Don’t wait",
        "kind": "single",
        "summary": "Stream first. Allow-list runs in the background. TTFT does not wait on Postgres.",
        "steps": ["Run twice. Keep the second TTFT as the model band.", "connect_ms and query_ms stay 0."],
        "look_for": "TTFT is the model only.",
    },
    {
        "id": "nat",
        "title": "2. New connect via NAT",
        "kind": "single",
        "summary": "await connect() + SELECT, then stream. New TCP every time. Handshake (NAT + TLS) sits in front of the first token.",
        "steps": ["DB ping (no model): note connect_ms vs query_ms.", "Run. Extra TTFT should track connect_ms."],
        "look_for": "connect_ms ≫ query_ms. The SELECT is not the cost.",
    },
    {
        "id": "scale",
        "title": "3. New Cloud Run instance",
        "kind": "single",
        "summary": "No in-process socket (new instance). Same as card 2. Real idle: min-instances=0, wait for 0 instances, then one ping.",
        "steps": ["Run or ping now (DB handshake only).", "Optional: wait for scale-to-zero, then ping once (adds container start)."],
        "look_for": "connect_ms like card 2. First hit after 0 instances is larger.",
    },
    {
        "id": "timeout",
        "title": "4. Egress IP not listed",
        "kind": "explain",
        "summary": "Traffic not from the NAT IP on Neon’s list. connect_timeout is 10s. That wait is TTFT if you await connect() first.",
        "steps": [
            "No Run during the talk.",
            "Cause: IP allow list on, but Cloud Run egress is not all-traffic through that NAT, or Neon has the wrong IP.",
            "Symptom: error or ~10s, not a slow SELECT.",
        ],
        "look_for": "Closed door, not query time.",
    },
]
