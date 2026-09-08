# Mock Agent benchmark

Run the API with `AGENT_MODE=mock`, register `LOCUST_EMAIL`, and execute:

```bash
locust -f locustfile.py --headless -u 5 -r 1 -t 30s --host http://127.0.0.1:8000
```

Record the generated Locust summary for request count, failures, average
latency, P95 latency, and the number of tasks reaching a terminal state. These
numbers describe the deterministic Mock Agent path only; they are not a proxy
for GitHub, LLM, GPU, or MCP production throughput.
