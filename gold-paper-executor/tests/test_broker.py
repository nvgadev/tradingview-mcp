import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import broker as B  # noqa: E402
import webull_lite as W  # noqa: E402


class FakeWB:
    """Records calls; order detail answers come from `detail_for`."""
    is_sandbox = True

    def __init__(self):
        self.calls, self.detail_for, self.positions = [], {}, []

    def get(self, uri, **q):
        self.calls.append(("GET", uri, q))
        if uri == "/openapi/account/list":
            return [{"account_id": "ACC", "account_class": "FUTURES"}]
        if uri == "/trading/orders/get":
            return self.detail_for.get(q.get("client_order_id"), {"order_status": "WORKING"})
        if uri == "/openapi/assets/positions":
            return self.positions
        return {}

    def post(self, uri, body):
        self.calls.append(("POST", uri, body))
        return {"client_order_id": body["new_orders"][0]["client_order_id"]}

    def request(self, method, uri, queries=None, body=None):
        self.calls.append((method, uri, body or queries))
        return {}

    def placed(self):
        return [c[2]["new_orders"][0] for c in self.calls if c[0] == "POST"]


def mk(mode="paper", verified=True):
    home = tempfile.mkdtemp()
    wb = FakeWB()
    b = B.Broker({"mode": mode, "account_id": "ACC", "fill_wait_s": 0}, home, wb=wb, sleep=lambda s: None)
    b.forms = {"verified": verified, "detail_param": "client_order_id",
               "cancel": {"method": "DELETE", "where": "body"}}
    return b, wb


OPEN = {"op": "open", "tid": "100", "root": "MGC", "symbol": "MGCZ6", "dir": 1, "qty": 1,
        "sig_px": 4000.0, "sl_sig": 3993.0}


class Broker(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        b, wb = mk("dry_run", verified=False)
        self.assertEqual(b.run(OPEN), [])
        self.assertEqual(wb.calls, [])

    def test_paper_refused_until_verified_and_only_on_sandbox(self):
        b, wb = mk(verified=False)
        self.assertIn("NOT sent", b.run(OPEN)[0])
        self.assertEqual([c for c in wb.calls if c[0] == "POST"], [])
        b, wb = mk()
        wb.is_sandbox = False
        self.assertIn("NOT sent", b.run(OPEN)[0])

    def test_open_places_market_then_stop_from_actual_fill(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"order_status": "FILLED", "filled_price": "4000.4"}
        msg = b.run(OPEN)
        o = wb.placed()
        self.assertEqual((o[0]["order_type"], o[0]["side"], o[0]["symbol"]), ("MARKET", "BUY", "MGCZ6"))
        self.assertEqual((o[1]["order_type"], o[1]["side"], o[1]["stop_price"]), ("STOP_LOSS", "SELL", "3993.40"))
        self.assertIn("filled at 4000.4", msg[0])
        self.assertEqual(b.state["100"]["status"], "open")

    def test_filled_without_price_still_gets_a_stop_from_signal_price(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"orders": [{"status": "FILLED", "filled_quantity": "1"}]}
        msg = b.run(OPEN)
        self.assertEqual(wb.placed()[1]["stop_price"], "3993.00")
        self.assertIn("not reported", msg[0])

    def test_nested_status_like_the_real_sandbox_reply(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"client_order_id": "gcE100", "orders": [
            {"symbol": "MGCZ6", "status": "FILLED", "filled_quantity": "1", "filled_price": "4001.2"}]}
        b.run(OPEN)
        self.assertEqual(b.state["100"]["entry_fill"], 4001.2)

    def test_unconfirmed_fill_places_no_stop_and_warns(self):
        b, wb = mk()
        msg = b.run(OPEN)
        self.assertEqual(len(wb.placed()), 1)
        self.assertIn("could not be confirmed", msg[0])

    def test_stop_move_places_new_before_cancelling_old_and_only_tightens(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"order_status": "FILLED", "filled_price": "4000.0"}
        b.run(OPEN)
        wb.calls.clear()
        b.run({"op": "stop", "tid": "100", "offset": 1.4})
        kinds = [c[0] for c in wb.calls]
        self.assertEqual(kinds, ["POST", "DELETE"])
        self.assertEqual(wb.placed()[0]["stop_price"], "4001.40")
        wb.calls.clear()
        self.assertEqual(b.run({"op": "stop", "tid": "100", "offset": 0.5}), [])   # looser: ignored
        self.assertEqual(wb.calls, [])

    def test_close_when_stop_already_filled_sends_no_order(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"order_status": "FILLED", "filled_price": "4000.0"}
        b.run(OPEN)
        wb.detail_for["gcS1000"] = {"order_status": "FILLED", "filled_price": "3993.2"}
        wb.calls.clear()
        msg = b.run({"op": "close", "tid": "100", "kind": "SL"})
        self.assertEqual(wb.placed(), [])
        self.assertIn("3993.2", msg[0])

    def test_close_cancels_stop_and_market_closes(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"order_status": "FILLED", "filled_price": "4000.0"}
        b.run(OPEN)
        wb.positions = [{"symbol": "MGCZ6", "quantity": "1"}]
        wb.detail_for["gcX100"] = {"order_status": "FILLED", "filled_price": "4015.5"}
        wb.calls.clear()
        msg = b.run({"op": "close", "tid": "100", "kind": "TP3"})
        self.assertIn(("DELETE", "/trading/orders/cancel", {"account_id": "ACC", "client_order_id": "gcS1000"}), wb.calls)
        self.assertEqual(wb.placed()[0]["order_type"], "MARKET")
        self.assertEqual(wb.placed()[0]["side"], "SELL")
        self.assertIn("4015.5", msg[0])

    def test_close_never_opens_the_other_side_when_already_flat(self):
        b, wb = mk()
        wb.detail_for["gcE100"] = {"order_status": "FILLED", "filled_price": "4000.0"}
        b.run(OPEN)
        wb.positions = []                                   # flat at the broker
        wb.calls.clear()
        msg = b.run({"op": "close", "tid": "100", "kind": "FLIP"})
        self.assertEqual(wb.placed(), [])
        self.assertIn("already flat", msg[0])

    def test_dig_and_status(self):
        self.assertEqual(B.dig({"a": [{"Filled_Price": "1.5"}]}, ["filled_price"]), "1.5")
        self.assertEqual(B.status_kind("PARTIAL_FILLED"), "partial")
        self.assertEqual(B.status_kind("Filled"), "filled")
        self.assertEqual(B.status_kind("CANCELLED"), "dead")


if __name__ == "__main__":
    unittest.main()
