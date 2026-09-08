import os
import time
import uuid

from locust import HttpUser, between, task


class SearchUser(HttpUser):
    wait_time = between(0.2, 0.5)

    def on_start(self):
        email = os.getenv("LOCUST_EMAIL", "load-test@example.com")
        password = os.getenv("LOCUST_PASSWORD", "password-123")
        response = self.client.post("/auth/login", json={"email": email, "password": password})
        if response.status_code != 200:
            raise RuntimeError("LOCUST_EMAIL must reference a registered user")
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    @task
    def search_and_poll(self):
        headers = {**self.headers, "Idempotency-Key": str(uuid.uuid4())}
        with self.client.post(
            "/search",
            headers=headers,
            json={"query": "python inference", "max_results": 10, "per_page": 5},
            name="POST /search",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"unexpected status {response.status_code}")
                return
            task_id = response.json().get("task_id")
            if not task_id:
                response.failure("missing task_id")
                return

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with self.client.get(
                f"/tasks/{task_id}", headers=self.headers, name="GET /tasks/{id}", catch_response=True
            ) as poll:
                if poll.status_code != 200:
                    poll.failure(f"unexpected status {poll.status_code}")
                    return
                status = poll.json().get("status")
                if status in {"SUCCEEDED", "FAILED"}:
                    if status == "FAILED":
                        poll.failure("task failed")
                    return
            time.sleep(0.2)
