SCENARIOS = [
    {
        "id": "good",
        "title": "1. Don’t wait",
        "kind": "single",
        "summary": "Allow-list runs in the background. First token does not wait for Postgres. This is the control.",
        "steps": ["Click Run twice. Keep the second TTFT."],
        "look_for": "allowlist_ms is 0 on the critical path. TTFT is just the model.",
    },
    {
        "id": "serial",
        "title": "2. Await, then stream",
        "kind": "single",
        "summary": "await allowlist() before opening the model. Same SELECT; it sits in front of the first token.",
        "steps": ["Click Run. Compare TTFT to card 1."],
        "look_for": "TTFT ≈ card 1 + allowlist_ms.",
    },
    {
        "id": "gather",
        "title": "3. Gather, then yield",
        "kind": "single",
        "summary": "Allow-list and the model connect run together, but you still wait for both before yielding. Parallel is not the same as overlapping TTFT.",
        "steps": ["Click Run. Compare to cards 1 and 2."],
        "look_for": "TTFT is max(allowlist, model connect), not model-only.",
    },
    {
        "id": "sync",
        "title": "4. Sync allow-list (pair)",
        "kind": "pair",
        "summary": "A blocking allow-list call on the event loop. The other in-flight stream cannot deliver its first token until it returns.",
        "steps": [
            "Click Run pair. Victim is card 1. Blocker then runs a sync SELECT (plus pg_sleep(1) so the stall is obvious).",
            "Compare victim client TTFT to card 1.",
        ],
        "look_for": "Blocker TTFT is tiny. Victim TTFT is ~1s higher even though it never awaited the allow-list.",
    },
]
