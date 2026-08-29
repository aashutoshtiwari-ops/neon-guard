SCENARIOS = [
    {
        "id": "good",
        "title": "1. Don’t wait",
        "kind": "single",
        "summary": "Control. Stream the model first. The allow-list (and thus Cloud NAT) is not on the path to the first token.",
        "steps": [
            "Click Run twice. Keep the second TTFT as the model baseline.",
            "connect_ms and query_ms stay 0 on the critical path.",
        ],
        "look_for": "TTFT is only the model. Neon IP allow list cannot add latency if you do not wait on a new connection.",
    },
    {
        "id": "nat",
        "title": "2. New connect via NAT",
        "kind": "single",
        "summary": "Cloud Run has no stable IP, so Neon’s IP allow list requires VPC + Cloud NAT. This card opens a new Postgres connection on the critical path — that handshake is the extra hop.",
        "steps": [
            "Optional: DB ping (no model) and note connect_ms vs query_ms.",
            "Click Run. Compare TTFT to card 1. The extra should track connect_ms, not the SELECT.",
        ],
        "look_for": "connect_ms ≫ query_ms. That gap is TLS + NAT, not allow-list SQL. A listed VM public IP does not pay this NAT box.",
    },
    {
        "id": "scale",
        "title": "3. New Cloud Run instance",
        "kind": "single",
        "summary": "Cloud Run instances are disposable. Scale-from-zero or a new max-instance has no in-process pool, so the first request pays a full NAT+TLS connect. This card drops the pool then connects (the database part of a new instance).",
        "steps": [
            "Click Run. The process forgets its socket, then does card 2.",
            "For the real thing: min-instances=0, wait until the service scales to zero, then Run once (that also includes container start).",
        ],
        "look_for": "Same shape as card 2 (large connect_ms). On a VM the process kept the pool for days; on Cloud Run this happens on every new instance.",
    },
    {
        "id": "timeout",
        "title": "4. Egress IP not listed",
        "kind": "explain",
        "summary": "If Cloud Run does not egress through the NAT IP you put on Neon’s list, connect fails until connect_timeout (10s). That wait is TTFT if the check is on the critical path.",
        "steps": [
            "No Run — do not break the live service mid-talk.",
            "This happens with default Cloud Run internet egress (rotating IPs) while IP allow list is on, or a VPC connector that is not using that NAT.",
            "Symptom: errors or ~10s TTFT, not a slow SELECT.",
        ],
        "look_for": "Pass/fail door. VM with listed public IP does not hit this. Cloud Run without static NAT does.",
    },
]
