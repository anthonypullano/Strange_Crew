import http.cookies
import threading
import unittest
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

import app


class FlightFilteringTests(unittest.TestCase):
    def test_category_filters_include_mixed_flights(self):
        cargo = app.filter_flights("cargo", "")
        crew = app.filter_flights("crew", "")

        self.assertEqual(len(cargo), 4)
        self.assertEqual(len(crew), 3)
        self.assertIn("KITE 09", {flight["id"] for flight in cargo})
        self.assertIn("KITE 09", {flight["id"] for flight in crew})

    def test_search_matches_story_and_route_fields(self):
        results = app.filter_flights("all", "garden gnomes")
        self.assertEqual([flight["id"] for flight in results], ["TERN 18"])

    def test_search_text_is_escaped_in_rendered_page(self):
        page = app.render_dashboard("all", '" onfocus="alert(1)', "", set())
        self.assertIn('value="&quot; onfocus=&quot;alert(1)"', page)
        self.assertNotIn('value="" onfocus="alert(1)"', page)


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.StrangeCrewHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, headers=None):
        request = Request(self.base_url + path, data=data, headers=headers or {})
        with urlopen(request) as response:
            return response.status, response.read().decode(), response.headers

    def test_search_filter_and_watchlist_post(self):
        status, page, _ = self.request("/?filter=crew&q=cloud")
        self.assertEqual(status, 200)
        self.assertIn("CIRRUS 22", page)
        self.assertNotIn("MOTH 07", page)

        body = urlencode({"flight": "CIRRUS 22", "filter": "crew", "q": "cloud"}).encode()
        _, watched_page, headers = self.request("/watch", data=body)
        cookie = http.cookies.SimpleCookie()
        cookie.load(headers["Set-Cookie"])
        cookie_header = f'{app.COOKIE_NAME}={cookie[app.COOKIE_NAME].value}'
        self.assertIn("WATCHING", watched_page)
        self.assertIn('<span class="stat-value">01</span>', watched_page)

        _, unwatch_page, _ = self.request(
            "/watch",
            data=body,
            headers={"Cookie": cookie_header},
        )
        self.assertIn("☆ WATCH", unwatch_page)
        self.assertIn('<span class="stat-value">00</span>', unwatch_page)


if __name__ == "__main__":
    unittest.main()
