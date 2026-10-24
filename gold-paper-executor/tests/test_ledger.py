import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ledger  # noqa: E402

T0 = 1_760_000_000          # fixed 'now'
BAR = (T0 - 120) * 1000     # bar closed 2 minutes ago for tf=5 -> bar open = T0-120-300 ... see _payload


def cfg(**kw):
    c = ledger.load_config("/nonexistent")
    c.update(account_start_usd=5000.0, instrument_map={}, tick_size=0.1,
             commission_per_side_usd=2.5)                    # classic 1 x GC on a $5k account
    c["margin"]["GC"]["intraday_usd"] = 8000.0
    c.update(kw)
    return c


def payload(events, now=T0, age_min=1, sym="COMEX:GC1!", pv=100, tf="5"):
    bar_open = now - age_min * 60 - 300       # the bar closed `age_min` minutes before `now`
    return {"sym": sym, "tf": tf, "bar": bar_open * 1000, "pv": pv, "events": events}


def entry(tid="1", d="LONG", px=4000.0, sl=3993.0):
    sgn = 1 if d == "LONG" else -1
    r = abs(px - sl)
    return {"e": "ENTRY", "tid": tid, "dir": d, "px": px, "sl": sl, "tp1": px + sgn * r,
            "tp2": px + sgn * 2 * r, "tp3": px + sgn * 3 * r, "conf": 8, "tag": ""}


def exit_(tid="1", kind="TP3", px=4021.0):
    return {"e": "EXIT", "tid": tid, "dir": "LONG", "kind": kind, "px": px}


class Rules(unittest.TestCase):
    def setUp(self):
        self.c = cfg()
        self.s = ledger.new_state(self.c)

    def run_(self, evs, now=T0, **kw):
        return ledger.handle_payload(self.s, self.c, payload(evs, now=now, **kw), now)

    def test_long_winner_math(self):
        self.run_([entry()])
        self.assertEqual(self.s["position"]["fill_px"], 4000.1)       # +1 tick
        out = self.run_([exit_(kind="TP3", px=4021.0)], now=T0 + 3600)
        row = out["closed"][0]
        self.assertEqual(row["exit_fill"], 4021.0)                    # limit: no slippage
        self.assertAlmostEqual(row["points"], 20.9)
        self.assertAlmostEqual(row["net_usd"], 20.9 * 100 - 5.0)
        self.assertAlmostEqual(self.s["cash"], 5000 + 2085)
        self.assertIsNone(self.s["position"])

    def test_short_stop_loss_is_adverse(self):
        self.run_([entry(d="SHORT", px=4000.0, sl=4007.0)])
        self.assertEqual(self.s["position"]["fill_px"], 3999.9)       # short sells a tick lower
        out = self.run_([dict(exit_(kind="SL", px=4007.0), dir="SHORT")], now=T0 + 600)
        row = out["closed"][0]
        self.assertEqual(row["exit_fill"], 4007.1)                    # buys back a tick worse
        self.assertAlmostEqual(row["points"], -(4007.1 - 3999.9))
        self.assertLess(self.s["cash"], 5000 - 700)

    def test_targets_move_stop_and_do_not_close(self):
        self.run_([entry()])
        self.run_([{"e": "TP1", "tid": "1", "px": 4007.0, "new_sl": 4001.4}])
        self.assertEqual(self.s["position"]["stage"], 1)
        self.assertEqual(self.s["position"]["sl"], 4001.4)
        self.run_([{"e": "TP2", "tid": "1", "px": 4014.0, "new_sl": 4007.0}])
        self.assertEqual(self.s["position"]["stage"], 2)
        self.assertIsNotNone(self.s["position"])

    def test_duplicate_events_apply_once(self):
        self.run_([entry()])
        out = self.run_([entry()])
        self.assertEqual(out["applied"][0][1], "duplicate")
        out = self.run_([exit_(kind="SL", px=3993.0)], now=T0 + 60)
        self.run_([exit_(kind="SL", px=3993.0)], now=T0 + 120)
        self.assertEqual(self.s["total"]["trades"], 1)

    def test_stale_entry_skipped_but_stale_exit_applied(self):
        out = self.run_([entry("A")], age_min=45)
        self.assertIsNone(self.s["position"])
        self.assertIn("too late", out["messages"][0])
        self.run_([entry("B")], age_min=1)
        out = self.run_([exit_("B", kind="FLIP", px=3999.0)], now=T0 + 99999, age_min=60)
        self.assertEqual(len(out["closed"]), 1)
        self.assertEqual(out["closed"][0]["late_exit"], 1)

    def test_events_of_a_skipped_trade_are_ignored_quietly(self):
        self.run_([entry("A")], age_min=45)
        out = self.run_([{"e": "TP1", "tid": "A", "px": 4007.0, "new_sl": 4001.4}, exit_("A")])
        self.assertEqual(out["messages"], [])

    def test_unknown_trade_exit_warns_and_changes_nothing(self):
        out = self.run_([exit_("ZZ")])
        self.assertIn("does not have", out["messages"][0])
        self.assertEqual(self.s["cash"], 5000)

    def test_daily_loss_cap_blocks_next_entry_then_resets_next_day(self):
        self.run_([entry("1")])
        self.run_([exit_("1", kind="SL", px=3993.0)], now=T0 + 60)      # about -$725
        self.run_([entry("2")], now=T0 + 120)
        self.run_([exit_("2", kind="SL", px=3993.0)], now=T0 + 180)     # second loss -> over 15%
        out = self.run_([entry("3")], now=T0 + 240)
        self.assertIsNone(self.s["position"])
        self.assertIn("daily loss limit", out["messages"][0])
        self.run_([entry("4")], now=T0 + 86400 + 120)                   # next ET day (same hour)
        self.assertIsNotNone(self.s["position"])

    def test_halt_floor_and_resume_by_state(self):
        self.s["cash"] = 2600
        self.run_([entry("1")])
        out = self.run_([exit_("1", kind="SL", px=3993.0)], now=T0 + 60)
        self.assertTrue(self.s["halted"])
        self.assertIn("HALTED", out["messages"][0])
        self.run_([entry("2")], now=T0 + 120)
        self.assertIsNone(self.s["position"])

    def test_paused_flag_blocks_entries_not_exits(self):
        self.run_([entry("1")])
        out = ledger.handle_payload(self.s, self.c, payload([entry("2")]), T0, paused=True)
        self.assertIn("paused", out["messages"][0])
        out = ledger.handle_payload(self.s, self.c, payload([exit_("1", kind="SL", px=3993.0)]), T0 + 60, paused=True)
        self.assertEqual(len(out["closed"]), 1)

    def test_margin_flag_and_enforcement(self):
        out = self.run_([entry("1")])
        self.assertIn("could NOT take this trade", out["messages"][0])
        self.assertIsNotNone(self.s["position"])
        self.c["enforce_margin"] = True
        s2 = ledger.new_state(self.c)
        out = ledger.handle_payload(s2, self.c, payload([entry("2")]), T0)
        self.assertIsNone(s2["position"])
        self.assertIn("too small", out["messages"][0])

    def test_mgc_uses_its_own_point_value(self):
        out = self.run_([entry("1", px=4000.0, sl=3993.0)], sym="COMEX:MGC1!", pv=10)
        self.assertAlmostEqual(self.s["position"]["risk_usd"], 7.1 * 10)
        self.assertNotIn("could NOT", out["messages"][0])   # $5k covers 1 MGC day margin

    def test_tradingview_micro_gold_ticker_is_accepted(self):
        self.run_([entry("1")], sym="COMEX_MINI:MGC1!", pv=10)
        self.assertIsNotNone(self.s["position"])

    def test_symbol_not_allowed(self):
        out = self.run_([entry("1")], sym="CME_MINI:ES1!", pv=50)
        self.assertIsNone(self.s["position"])
        self.assertIn("allowed list", out["messages"][0])

    def test_second_entry_while_open_is_skipped(self):
        self.run_([entry("1")])
        out = self.run_([entry("2")])
        self.assertIn("already open", out["messages"][0])
        self.assertEqual(self.s["position"]["tid"], "1")

    def test_same_bar_entry_and_exit(self):
        out = self.run_([entry("1"), exit_("1", kind="SL", px=3993.0)])
        self.assertEqual(len(out["closed"]), 1)
        self.assertIsNone(self.s["position"])

    def test_overdue_warning_once(self):
        self.run_([entry("1")])
        self.assertIsNone(ledger.overdue(self.s, self.c, T0 + 3600))
        self.assertIn("no exit signal", ledger.overdue(self.s, self.c, T0 + 31 * 3600))
        self.assertIsNone(ledger.overdue(self.s, self.c, T0 + 32 * 3600))

    def test_messages_have_no_jargon(self):
        self.run_([entry("1")])
        out = self.run_([exit_("1", kind="TP3", px=4021.0)], now=T0 + 600)
        txt = "\n".join(out["messages"])
        for bad in (" R ", "R)", "PF", "ATR", "expectancy", "drawdown"):
            self.assertNotIn(bad, txt)


class MicroMapping(unittest.TestCase):
    """GC1! signals on the chart, Micro Gold traded at the broker ($10 a point)."""

    def setUp(self):
        self.c = ledger.load_config("/nonexistent")           # real defaults: $10k + map
        self.s = ledger.new_state(self.c)

    def test_defaults_are_10k_and_one_ounce(self):
        self.assertEqual(self.c["account_start_usd"], 10000.0)
        self.assertEqual(self.c["instrument_map"]["GC"]["trade_root"], "1OZ")

    def test_entry_uses_micro_point_value_and_emits_open_intent(self):
        out = ledger.handle_payload(self.s, self.c, payload([entry("1", px=4000.0, sl=3993.0)]), T0)
        pos = self.s["position"]
        self.assertEqual(pos["pv"], 1.0)
        self.assertEqual(pos["broker_symbol"], "1OZZ6")
        self.assertAlmostEqual(pos["risk_usd"], 7.25 * 1 * 6)           # fill 4000.25, stop 3993
        it = out["intents"][0]
        self.assertEqual((it["op"], it["symbol"], it["dir"], it["qty"]), ("open", "1OZZ6", 1, 6))
        self.assertEqual(it["tick"], 0.25)
        self.assertEqual((it["sig_px"], it["sl_sig"]), (4000.0, 3993.0))
        self.assertNotIn("could NOT", out["messages"][0])      # $10k covers 1 MGC margin

    def test_targets_and_exit_emit_stop_and_close_intents(self):
        ledger.handle_payload(self.s, self.c, payload([entry("1", px=4000.0, sl=3993.0)]), T0)
        out = ledger.handle_payload(self.s, self.c, payload(
            [{"e": "TP1", "tid": "1", "px": 4007.0, "new_sl": 4001.4}]), T0 + 60)
        self.assertEqual(out["intents"], [{"op": "stop", "tid": "1", "offset": 1.4}])
        out = ledger.handle_payload(self.s, self.c, payload([exit_("1", kind="TP3", px=4021.0)]), T0 + 120)
        self.assertEqual(out["intents"], [{"op": "close", "tid": "1", "kind": "TP3"}])
        self.assertAlmostEqual(out["closed"][0]["points"], 20.75)
        self.assertAlmostEqual(self.s["cash"], 10000 + 20.75 * 6 - 2 * 0.6 * 6)

    def test_skipped_entry_emits_no_intent(self):
        out = ledger.handle_payload(self.s, self.c, payload([entry("1")], age_min=60), T0)
        self.assertNotIn("intents", out)


if __name__ == "__main__":
    unittest.main()


class BodyRepair(unittest.TestCase):
    def test_tradingview_continuous_ticker_with_quotes_is_repaired(self):
        import json
        import receiver
        raw = (b'{"secret":"x","sym":"={"settlement-as-close":true,"symbol":"COMEX_MINI:MGC1!"}",'
               b'"tf":"5","bar":1,"pv":10,"events":[]}')
        self.assertEqual(json.loads(receiver.repair_body(raw))["sym"], "COMEX_MINI:MGC1!")


class NewsGuard(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        self.c = ledger.load_config("/nonexistent")
        self.c.update(account_start_usd=5000.0, instrument_map={})
        self.cpi = datetime(2026, 11, 10, 8, 30, tzinfo=ledger.ET).timestamp()
        self.c["news_events"] = [{"name": "US CPI inflation", "ts": self.cpi, "before": 30,
                                  "after": 30, "at": "2026-11-10 08:30"}]
        self.s = ledger.new_state(self.c)

    def test_entry_inside_window_is_skipped_with_plain_reason(self):
        now = self.cpi - 20 * 60
        out = ledger.handle_payload(self.s, self.c, payload([entry("1")], now=now), now)
        self.assertIsNone(self.s["position"])
        self.assertIn("news time: US CPI inflation at 08:30 ET", out["messages"][0])

    def test_entry_outside_window_is_taken(self):
        now = self.cpi + 45 * 60
        ledger.handle_payload(self.s, self.c, payload([entry("1")], now=now), now)
        self.assertIsNotNone(self.s["position"])

    def test_flatten_due_only_in_last_minutes_and_once(self):
        now0 = self.cpi - 3 * 3600
        ledger.handle_payload(self.s, self.c, payload([entry("1")], now=now0), now0)
        self.assertIsNone(ledger.news_flatten_due(self.s, self.c, self.cpi - 20 * 60))
        self.assertIsNotNone(ledger.news_flatten_due(self.s, self.c, self.cpi - 4 * 60))
        self.s["position"]["news_flat"] = True
        self.assertIsNone(ledger.news_flatten_due(self.s, self.c, self.cpi - 3 * 60))

    def test_events_file_loads(self):
        import os
        c = ledger.load_config(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertGreater(len(c["news_events"]), 15)


class ContractRoll(unittest.TestCase):
    def test_picks_first_contract_until_its_last_day_then_the_next(self):
        imap = {"contracts": [{"symbol": "A", "until": "2026-11-20"}, {"symbol": "B", "until": "2027-01-22"}]}
        from datetime import datetime
        d = lambda y, m, dd: datetime(y, m, dd, 10, 0, tzinfo=ledger.ET).timestamp()
        self.assertEqual(ledger.pick_contract(imap, d(2026, 11, 20)), "A")
        self.assertEqual(ledger.pick_contract(imap, d(2026, 11, 21)), "B")
        self.assertIsNone(ledger.pick_contract(imap, d(2027, 1, 23)))

    def test_notice_five_days_before_the_switch(self):
        from datetime import datetime
        c = ledger.load_config("/nonexistent")
        now = datetime(2026, 11, 16, 10, 0, tzinfo=ledger.ET).timestamp()
        self.assertIn("1OZG7", ledger.contract_notice(c, now))
        self.assertIsNone(ledger.contract_notice(c, datetime(2026, 10, 20, 10, 0, tzinfo=ledger.ET).timestamp()))
