import unittest

from backend.app import app


class ApiValidationTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def _login(self, username="admin", password="admin123"):
        resp = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("token", data)
        return data["token"]

    def test_login_requires_json_object(self):
        resp = self.client.post("/api/auth/login", data="not-json", content_type="text/plain")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_login_success(self):
        resp = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin123"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("token", data)
        self.assertIn("user", data)

    def test_query_requires_auth(self):
        resp = self.client.post("/api/query", json={"query": "test"})
        self.assertEqual(resp.status_code, 401)

    def test_query_validation_type(self):
        token = self._login()
        resp = self.client.post(
            "/api/query",
            json={"query": 42, "language": "auto", "include_voice": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_query_success_response_shape(self):
        token = self._login()
        resp = self.client.post(
            "/api/query",
            json={"query": "Patient ke Ayushman claim ke liye kaunse documents chahiye?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("summary", data)
        self.assertIsInstance(data["summary"], str)

    def test_feedback_rejects_invalid_rating(self):
        token = self._login()
        resp = self.client.post(
            "/api/feedback",
            json={"query": "test", "rating": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_feedback_accepts_valid_payload(self):
        token = self._login()
        resp = self.client.post(
            "/api/feedback",
            json={"query": "test query", "rating": 1, "comment": "helpful"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("message", resp.get_json())


if __name__ == "__main__":
    unittest.main()
