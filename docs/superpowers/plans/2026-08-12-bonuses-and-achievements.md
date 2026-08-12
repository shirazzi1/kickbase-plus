# Login-Boni und Erfolge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A switch above the Balances table folds daily login bonuses and achievement rewards into the balance and the max bid, and the detail dialog shows them as rows marked as estimates.

**Architecture:** Two new pure builders in `backend/miscellaneous.py` produce bonus and achievement events in the same shape `build_balance_events()` already produces. `balances()` merges all three streams, recomputes the running balance over the merge, and writes both a transfer-only and a with-bonuses view into `balances.json`. Earned achievements are persisted in a new `achievements.json` that only grows, which is what gives them a stable date and keeps them from disappearing when the condition stops holding.

**Tech Stack:** Python 3.12 with `zoneinfo` (verified available; the Dockerfile installs `tzdata`), React 18 with MUI v5.

**Spec:** `docs/superpowers/specs/2026-08-12-bonuses-and-achievements-design.md`

## Global Constraints

- User-facing strings are German. Python comments use `###`, docstrings `"""### Summary`.
- JavaScript in `frontend/src` uses 4-space indent, double quotes, no semicolons.
- Tests are dependency-free scripts under `tests/`, run as `./venv/bin/python tests/<name>.py`, with the `check()` harness from `tests/test_start_date.py`.
- Every event dict carries the same keys as `build_balance_events()` produces: `date`, `type`, `amount`, `balance`, `playerName`, `playerImage`, `teamId`, `tradePartner`. New types add keys, never remove them.
- Money is rounded with `round(x)` to whole euros, as `build_balance_events()` already does.
- The reward catalogue lives in exactly one place, so adding the unknown silver/gold tiers later is pure number entry.

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/miscellaneous.py` | `build_login_bonus_events()`, the `ACHIEVEMENTS` catalogue, `build_achievement_events()`, `merge_balance_events()`. |
| `tests/test_login_bonus.py` | New. The bonus formula and the calendar-day counter. |
| `tests/test_achievements.py` | New. Catalogue conditions, the positive-balance rule, market-only rule, tier stacking. |
| `main.py` (`balances()`) | Loads and writes `achievements.json`, merges the streams, writes both views. |
| `frontend/src/components/Balances.js` | The switch, and which figures the table shows. |
| `frontend/src/components/BalanceEventsDialog.js` | Renders the chosen event list, marks estimates. |

---

### Task 1: The daily login bonus

**Files:**
- Modify: `backend/miscellaneous.py`
- Test: `tests/test_login_bonus.py` (create)

**Interfaces:**
- Produces: `build_login_bonus_events(start_datetime: datetime, until: datetime) -> list`. Returns event dicts with `type` `"login_bonus"`, oldest first, without a running balance — `merge_balance_events()` in Task 3 fills `balance` in.

**Background:** Day `n` pays `min(100_000, (n - 1) * 10_000)`. Day 1 pays nothing and produces no event. The counter runs over **calendar days in the app timezone**, not elapsed hours: the real feed has day 11 at 2026-08-11T01:13Z and day 12 at 2026-08-11T22:03Z, 21 hours apart but two different days in `Europe/Berlin`. The day of the season start is day 1.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the estimated daily login bonus.

Kickbase pays a login bonus that grows by 10.000 a day up to 100.000 and stays there.
The amounts below are the real type 22 feed events of one manager, so the formula is
checked against reality, not against itself.

The day counter runs over calendar days in the app timezone. The real feed proves it:
day 11 at 2026-08-11T01:13:09Z and day 12 at 2026-08-11T22:03:39Z are 21 hours apart
but fall on different days in Europe/Berlin.

    ./venv/bin/python tests/test_login_bonus.py
"""

import sys

from datetime import datetime, timezone
from os import environ, path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

PASSED = []

START = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)

### The real series collected by shirazzi, day -> amount
REAL = {2: 10_000, 3: 20_000, 4: 30_000, 5: 40_000, 6: 50_000, 7: 60_000,
        8: 70_000, 9: 80_000, 10: 90_000, 11: 100_000, 12: 100_000}


def check(name, fn):
    """Run a single test and record the result."""
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}\n        {e}")
        PASSED.append(False)
    except Exception as e:
        print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
        PASSED.append(False)
    else:
        print(f"  ok    {name}")
        PASSED.append(True)


def at(day, hour=12):
    """A UTC instant on the given day of August 2026."""
    return datetime(2026, 8, day, hour, 0, 0, tzinfo=timezone.utc)


def test_the_start_day_pays_nothing():
    assert miscellaneous.build_login_bonus_events(START, at(1, 20)) == [], \
        "expected no event on day one"


def test_the_second_day_pays_ten_thousand():
    events = miscellaneous.build_login_bonus_events(START, at(2))

    assert len(events) == 1, f"expected one event, got {events}"
    assert events[0]["amount"] == 10_000, f"expected 10000, got {events[0]}"
    assert events[0]["type"] == "login_bonus", f"expected a login_bonus event, got {events[0]}"


def test_the_amounts_match_the_real_feed():
    events = miscellaneous.build_login_bonus_events(START, at(12, 23))
    amounts = [e["amount"] for e in events]

    assert amounts == [REAL[d] for d in sorted(REAL)], \
        f"expected the real series {[REAL[d] for d in sorted(REAL)]}, got {amounts}"


def test_the_total_after_twelve_days():
    events = miscellaneous.build_login_bonus_events(START, at(12, 23))

    assert sum(e["amount"] for e in events) == 650_000, \
        f"expected 650000 in total, got {sum(e['amount'] for e in events)}"


def test_the_amount_is_capped_at_one_hundred_thousand():
    events = miscellaneous.build_login_bonus_events(START, at(31, 23))

    assert max(e["amount"] for e in events) == 100_000, "expected the cap to hold"
    assert events[-1]["amount"] == 100_000, "expected the last day to pay the cap"


def test_the_counter_uses_calendar_days_not_elapsed_hours():
    ### 2026-08-11T22:03Z is 2026-08-12 in Europe/Berlin, so it has to be day 12.
    ### Elapsed hours since the 18:00 start would still say day 10.
    events = miscellaneous.build_login_bonus_events(START, datetime(2026, 8, 11, 22, 3, tzinfo=timezone.utc))

    assert len(events) == 11, f"expected days 2 to 12, got {len(events)} events"
    assert events[-1]["amount"] == 100_000, f"expected day 12, got {events[-1]}"


def test_events_are_chronological_and_carry_no_player():
    events = miscellaneous.build_login_bonus_events(START, at(5))

    dates = [e["date"] for e in events]
    assert dates == sorted(dates), f"expected chronological order, got {dates}"
    for e in events:
        for field in ("playerName", "playerImage", "teamId", "tradePartner"):
            assert e[field] is None, f"expected {field} to be None, got {e}"


def test_nothing_before_the_season_starts():
    assert miscellaneous.build_login_bonus_events(START, at(1, 1)) == [], \
        "expected no events before the season start"


if __name__ == "__main__":
    print("build_login_bonus_events()")
    check("the start day pays nothing", test_the_start_day_pays_nothing)
    check("the second day pays ten thousand", test_the_second_day_pays_ten_thousand)
    check("the amounts match the real feed", test_the_amounts_match_the_real_feed)
    check("the total after twelve days is 650000", test_the_total_after_twelve_days)
    check("the amount is capped at 100000", test_the_amount_is_capped_at_one_hundred_thousand)
    check("the counter uses calendar days", test_the_counter_uses_calendar_days_not_elapsed_hours)
    check("events are chronological and carry no player",
          test_events_are_chronological_and_carry_no_player)
    check("nothing before the season starts", test_nothing_before_the_season_starts)

    total, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python tests/test_login_bonus.py`
Expected: every check errors with `AttributeError: ... has no attribute 'build_login_bonus_events'`.

- [ ] **Step 3: Implement**

Add the import at the top of `backend/miscellaneous.py`, next to the other datetime imports:

```python
from zoneinfo import ZoneInfo
```

Add the constants next to `PLAYER_IMAGE_BASE_URL`:

```python
### The daily login bonus grows by this much per day and stops at the cap. Confirmed
### against the real type 22 feed events: day 2 pays 10.000, day 11 and every day after
### pay 100.000.
LOGIN_BONUS_STEP = 10_000
LOGIN_BONUS_CAP = 100_000
```

And the builder, after `build_balance_events()`:

```python
def build_login_bonus_events(start_datetime: datetime, until: datetime) -> list:
    """### Build the estimated daily login bonus events for one manager.

    Day 1 is the day the season started and pays nothing. Every day after that pays
    10.000 more than the one before, up to 100.000, which is then paid every day.

    The day counter runs over calendar days in the app timezone rather than over elapsed
    hours. The real feed settles it: day 11 arrived at 01:13 UTC and day 12 at 22:03 UTC
    on the same UTC date, which are two different days in Europe/Berlin. Counting hours
    would fall a day behind and keep drifting.

    The bonus is an assumption. Type 22 feed events exist only for the logged in user, so
    there is no way to tell whether another manager logged in on a given day. Assuming it
    for everyone at least treats them alike.

    Args:
        start_datetime (datetime): The season start or league reset instant.
        until (datetime): The instant to count up to, normally now.

    Returns:
        list: Event dicts of type "login_bonus", oldest first, without a running balance.
    """
    zone = ZoneInfo(getenv("TZ", "Europe/Berlin"))

    first_day = start_datetime.astimezone(zone).date()
    last_day = until.astimezone(zone).date()

    events = []

    for offset in range(1, (last_day - first_day).days + 1):
        amount = min(LOGIN_BONUS_CAP, offset * LOGIN_BONUS_STEP)
        day = first_day + timedelta(days=offset)

        ### Dated at the start of that day in the app timezone. The real collection time
        ### varies with when the manager opened the app, which we cannot know.
        events.append({
            "date": datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc).isoformat(),
            "type": "login_bonus",
            "amount": amount,
            "balance": None,
            "playerName": None,
            "playerImage": None,
            "teamId": None,
            "tradePartner": None,
        })

    return events
```

`time` comes from `datetime`, so extend the existing import line to `from datetime import datetime, time, timedelta, timezone`.

- [ ] **Step 4: Run to verify they pass**

Run: `./venv/bin/python tests/test_login_bonus.py`
Expected: `8/8 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/miscellaneous.py tests/test_login_bonus.py
git commit -m "feat: estimate the daily login bonus per manager"
```

---

### Task 2: The achievement catalogue

**Files:**
- Modify: `backend/miscellaneous.py`
- Test: `tests/test_achievements.py` (create)

**Interfaces:**
- Produces: the `ACHIEVEMENTS` catalogue and
  `detect_achievements(trades: int, team_value: float, balance: float, turnovers: list, matchday_wins: int, matchday_points: list, placement: int, season_over: bool) -> list`,
  returning `[{"id": int, "name": str, "amount": int}]` for everything currently earned. Task 3 dates them and turns them into events.

**The rules, all from the spec:**

- Each achievement counts once per season. Three players with 3 Mio profit each still pay bronze once.
- Tiers stack: 6 Mio profit pays bronze *and* silver.
- The lucky touch family needs **both** the buy and the sell to go through the market. `tradePartner == "Kickbase"` on both sides of the turnover pair.
- Auto assigned players do not count: a turnover whose buy has `type == "assigned_at_start"` is out.
- Team value achievements only pay with a **positive** balance.
- Transfer King counts every trade, including trades between managers, so the dashboard `trades` field is the right source.
- League size achievements pay nothing into the balance and are therefore not in the catalogue at all.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the achievement rewards folded into the balance.

Rules and amounts come from help.kickbase.com/help/erfolge. The ones that matter here and
are easy to get wrong:

  - an achievement counts once per season, not once per qualifying player
  - tiers stack, so 6 Mio profit pays bronze and silver
  - the lucky touch family needs the buy AND the sell to go through the market
  - automatically assigned players do not count
  - team value achievements only pay when the balance is positive

That last rule is what explained three real balances that no simpler model fit.

    ./venv/bin/python tests/test_achievements.py
"""

import sys

from os import path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

PASSED = []


def check(name, fn):
    """Run a single test and record the result."""
    try:
        fn()
    except AssertionError as e:
        print(f"  FAIL  {name}\n        {e}")
        PASSED.append(False)
    except Exception as e:
        print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
        PASSED.append(False)
    else:
        print(f"  ok    {name}")
        PASSED.append(True)


def turnover(profit, buy_partner="Kickbase", sell_partner="Kickbase", buy_type="buy"):
    """A buy/sell pair in the shape turnovers.json holds."""
    return [
        {"type": buy_type, "tradePartner": buy_partner, "price": 1_000_000,
         "playerId": "1", "lastName": "Player"},
        {"type": "sell", "tradePartner": sell_partner, "price": 1_000_000 + profit,
         "playerId": "1", "lastName": "Player"},
    ]


def detect(trades=1, team_value=0, balance=0, turnovers=None, matchday_wins=0,
           matchday_points=None, placement=0, season_over=False):
    """Run the function under test with sensible defaults."""
    return miscellaneous.detect_achievements(trades, team_value, balance, turnovers or [],
                                             matchday_wins, matchday_points or [],
                                             placement, season_over)


def ids(earned):
    return sorted(a["id"] for a in earned)


def total(earned):
    return sum(a["amount"] for a in earned)


### ===============================================================================

def test_no_trades_earns_nothing():
    assert detect(trades=0) == [], "expected no achievement without a single trade"


def test_the_first_trade_earns_first_deal():
    earned = detect(trades=1)

    assert ids(earned) == [500], f"expected only First deal, got {earned}"
    assert total(earned) == 100_000, f"expected 100000, got {earned}"


def test_fifty_trades_earn_transfer_king():
    earned = detect(trades=50)

    assert ids(earned) == [500, 501], f"expected First deal and Transfer King, got {earned}"
    assert total(earned) == 350_000, f"expected 350000, got {earned}"


def test_forty_nine_trades_do_not():
    assert ids(detect(trades=49)) == [500], "expected Transfer King to need fifty trades"


def test_team_value_pays_with_a_positive_balance():
    earned = detect(trades=1, team_value=125_000_000, balance=1)

    assert ids(earned) == [400, 500], f"expected the team value reward, got {earned}"


def test_team_value_is_withheld_with_a_negative_balance():
    ### The rule that explained shirazzi and Reddy
    earned = detect(trades=1, team_value=125_000_000, balance=-1)

    assert ids(earned) == [500], f"expected the team value reward to be withheld, got {earned}"


def test_team_value_below_the_threshold_pays_nothing():
    earned = detect(trades=1, team_value=124_999_999, balance=10_000_000)

    assert ids(earned) == [500], f"expected the threshold to hold, got {earned}"


def test_a_market_sale_at_three_million_profit_pays_bronze():
    earned = detect(turnovers=[turnover(3_000_000)])

    assert total(earned) == 100_000 + 250_000, f"expected bronze on top of First deal, got {earned}"


def test_tiers_stack():
    earned = detect(turnovers=[turnover(6_000_000)])

    assert total(earned) == 100_000 + 250_000 + 500_000, \
        f"expected bronze and silver, got {earned}"


def test_an_achievement_counts_once_per_season():
    ### Three qualifying players still pay bronze exactly once
    earned = detect(turnovers=[turnover(3_000_000), turnover(3_500_000), turnover(4_000_000)])

    assert total(earned) == 100_000 + 250_000, f"expected bronze once, got {earned}"


def test_a_sale_to_another_manager_does_not_count():
    earned = detect(turnovers=[turnover(6_000_000, sell_partner="Jonny")])

    assert total(earned) == 100_000, f"expected a manager trade not to pay, got {earned}"


def test_a_purchase_from_another_manager_does_not_count():
    earned = detect(turnovers=[turnover(6_000_000, buy_partner="Jonny")])

    assert total(earned) == 100_000, f"expected a manager purchase not to pay, got {earned}"


def test_an_automatically_assigned_player_does_not_count():
    earned = detect(turnovers=[turnover(6_000_000, buy_type="assigned_at_start")])

    assert total(earned) == 100_000, f"expected an assigned player not to pay, got {earned}"


def test_matchday_wins_pay_per_win():
    earned = detect(matchday_wins=3)
    wins = [a for a in earned if a["id"] == 700]

    assert len(wins) == 3, f"expected one entry per win, got {wins}"
    assert sum(a["amount"] for a in wins) == 3_000_000, f"expected 3 million, got {wins}"


def test_matchday_points_pay_per_tier_and_stack():
    ### 1600 points clears silver and gold, but not the 2000 of Jahrhundertspiel
    earned = detect(matchday_points=[400, 1600, 900])
    points = [a for a in earned if a["id"] in (701, 702, 703)]

    assert sorted(a["id"] for a in points) == [701, 702], f"expected silver and gold, got {points}"
    assert sum(a["amount"] for a in points) == 750_000, f"expected 750000, got {points}"


def test_two_thousand_points_earn_jahrhundertspiel():
    earned = detect(matchday_points=[2000])
    points = [a for a in earned if a["id"] in (701, 702, 703)]

    assert sorted(a["id"] for a in points) == [701, 702, 703], f"expected all three, got {points}"
    assert sum(a["amount"] for a in points) == 1_750_000, f"expected 1750000, got {points}"


def test_matchday_points_count_once_per_season():
    ### Two matchdays over 1000 still pay silver once
    earned = detect(matchday_points=[1200, 1300])
    points = [a for a in earned if a["id"] == 701]

    assert len(points) == 1, f"expected silver once, got {points}"


def test_the_season_title_only_pays_once_the_season_is_over():
    assert [a for a in detect(placement=1, season_over=False) if a["id"] == 800] == [], \
        "expected no title before the season ends"

    champion = [a for a in detect(placement=1, season_over=True) if a["id"] == 800]
    assert len(champion) == 1 and champion[0]["amount"] == 2_000_000, \
        f"expected the championship reward, got {champion}"


def test_second_place_earns_the_runner_up_reward():
    runner_up = [a for a in detect(placement=2, season_over=True) if a["id"] == 801]

    assert len(runner_up) == 1 and runner_up[0]["amount"] == 1_000_000, \
        f"expected the runner up reward, got {runner_up}"


def test_third_place_earns_no_title():
    assert [a for a in detect(placement=3, season_over=True) if a["id"] in (800, 801)] == [], \
        "expected no title for third place"


def test_the_catalogue_has_no_league_size_rewards():
    ### They pay nothing into the balance, so they must not be in the catalogue
    for achievement_id in (600, 601, 602):
        assert achievement_id not in miscellaneous.ACHIEVEMENTS, \
            f"expected {achievement_id} to be absent from the catalogue"


if __name__ == "__main__":
    print("detect_achievements()")
    check("no trades earns nothing", test_no_trades_earns_nothing)
    check("the first trade earns First deal", test_the_first_trade_earns_first_deal)
    check("fifty trades earn Transfer King", test_fifty_trades_earn_transfer_king)
    check("forty nine trades do not", test_forty_nine_trades_do_not)
    check("team value pays with a positive balance", test_team_value_pays_with_a_positive_balance)
    check("team value is withheld with a negative balance",
          test_team_value_is_withheld_with_a_negative_balance)
    check("team value below the threshold pays nothing",
          test_team_value_below_the_threshold_pays_nothing)
    check("a market sale at three million pays bronze",
          test_a_market_sale_at_three_million_profit_pays_bronze)
    check("tiers stack", test_tiers_stack)
    check("an achievement counts once per season", test_an_achievement_counts_once_per_season)
    check("a sale to another manager does not count", test_a_sale_to_another_manager_does_not_count)
    check("a purchase from another manager does not count",
          test_a_purchase_from_another_manager_does_not_count)
    check("an automatically assigned player does not count",
          test_an_automatically_assigned_player_does_not_count)
    check("matchday wins pay per win", test_matchday_wins_pay_per_win)
    check("matchday points pay per tier and stack", test_matchday_points_pay_per_tier_and_stack)
    check("2000 points earn Jahrhundertspiel", test_two_thousand_points_earn_jahrhundertspiel)
    check("matchday points count once per season", test_matchday_points_count_once_per_season)
    check("the season title only pays once the season is over",
          test_the_season_title_only_pays_once_the_season_is_over)
    check("second place earns the runner up reward", test_second_place_earns_the_runner_up_reward)
    check("third place earns no title", test_third_place_earns_no_title)
    check("the catalogue has no league size rewards",
          test_the_catalogue_has_no_league_size_rewards)

    total_checks, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total_checks} passed")
    sys.exit(0 if passed == total_checks else 1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `./venv/bin/python tests/test_achievements.py`
Expected: every check errors on the missing attribute.

- [ ] **Step 3: Implement the catalogue and the detection**

Add after `build_login_bonus_events()`:

```python
### Achievement rewards, from help.kickbase.com/help/erfolge.
###
### Not in here on purpose: the league size achievements (600 Kreisliga, 601 Regionalliga,
### 602 2. Liga). The app shows 1.000.000 each, but they do not reach the balance - three
### real balances only add up without them, and a cutoff cannot explain it either, since
### "First deal" was awarded before the league reset too and does count.
###
### Also missing: the silver and gold tiers of Transfer King and Team value, whose
### thresholds and amounts are unknown. Adding them is a line each.
ACHIEVEMENTS = {
    500: {"name": "First deal", "amount": 100_000},
    501: {"name": "Transfer King bronze", "amount": 250_000},
    400: {"name": "Team value bronze", "amount": 100_000},
    700: {"name": "Spieltagssieger", "amount": 1_000_000},
    701: {"name": "Spieltagspunkte Silber", "amount": 250_000},
    702: {"name": "Spieltagspunkte Gold", "amount": 500_000},
    703: {"name": "Jahrhundertspiel", "amount": 1_000_000},
    800: {"name": "Meister", "amount": 2_000_000},
    801: {"name": "Vizemeister", "amount": 1_000_000},
}

### Points in a single matchday and what they pay. Tiers stack, each once per season.
### The ids are ours: the feed never showed these, so there is no Kickbase id to reuse.
MATCHDAY_POINT_TIERS = [(1_000, 701), (1_500, 702), (2_000, 703)]

### Minimum trades for the transfer count achievements
TRANSFER_KING_BRONZE_TRADES = 50

### Team value achievements need this much value, and a balance in the black
TEAM_VALUE_BRONZE = 125_000_000

### Profit with a single player and what it pays. Tiers stack, so 6 Mio pays the first two.
LUCKY_TOUCH_TIERS = [
    (3_000_000, "Bronzenes Händchen", 250_000),
    (5_000_000, "Silbernes Händchen", 500_000),
    (10_000_000, "Goldenes Händchen", 1_000_000),
    (25_000_000, "Königstransfer", 2_000_000),
]


def detect_achievements(trades: int, team_value: float, balance: float, turnovers: list,
                        matchday_wins: int, matchday_points: list, placement: int,
                        season_over: bool) -> list:
    """### Work out which achievements a manager has earned.

    Only the ones that can be derived from data the project already fetches. Every
    achievement counts once per season, except the matchday win, which pays per win.

    Args:
        trades (int): Transfers made this season. Trades between managers count.
        team_value (float): The manager's current team value.
        balance (float): The manager's balance. Team value rewards are withheld when it
            is negative.
        turnovers (list): The manager's buy/sell pairs, as turnovers.json holds them.
        matchday_wins (int): How many matchdays the manager won.
        matchday_points (list): Points scored on each matchday played so far.
        placement (int): The manager's position in the league.
        season_over (bool): Whether every matchday has been played. The season titles pay
            only then, since the placement moves until the last whistle.

    Returns:
        list: Dicts of {"id", "name", "amount"} for everything earned.
    """
    earned = []

    def award(achievement_id, name=None, amount=None):
        entry = ACHIEVEMENTS.get(achievement_id, {})
        earned.append({
            "id": achievement_id,
            "name": name or entry["name"],
            "amount": amount if amount is not None else entry["amount"],
        })

    if trades >= 1:
        award(500)

    if trades >= TRANSFER_KING_BRONZE_TRADES:
        award(501)

    ### Withheld in the red. This is the rule that explained three real balances.
    if team_value >= TEAM_VALUE_BRONZE and balance > 0:
        award(400)

    ### One entry per win: this is the only repeatable one we can derive
    for _ in range(matchday_wins):
        award(700)

    ### Point tiers stack, and the best single matchday decides which are reached
    best_matchday = max(matchday_points, default=0)
    for threshold, achievement_id in MATCHDAY_POINT_TIERS:
        if best_matchday >= threshold:
            award(achievement_id)

    ### The placement only settles when the last matchday has been played
    if season_over and placement == 1:
        award(800)
    elif season_over and placement == 2:
        award(801)

    ### The lucky touch family. Both sides have to go through the market, the player must
    ### not have been assigned at the start, and the best single profit decides which
    ### tiers are reached - each of them once for the whole season.
    best_profit = 0
    for buy, sell in turnovers:
        if buy.get("type") == "assigned_at_start":
            continue
        if buy.get("tradePartner") != "Kickbase" or sell.get("tradePartner") != "Kickbase":
            continue

        best_profit = max(best_profit, sell["price"] - buy["price"])

    for threshold, name, amount in LUCKY_TOUCH_TIERS:
        if best_profit >= threshold:
            award(threshold, name=name, amount=amount)

    return earned
```

Note the lucky touch tiers use the threshold as their id, which keeps them distinct from the numbered Kickbase ids without inventing collisions.

- [ ] **Step 4: Run to verify they pass**

Run: `./venv/bin/python tests/test_achievements.py`
Expected: `21/21 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/miscellaneous.py tests/test_achievements.py
git commit -m "feat: work out which achievements a manager has earned"
```

---

### Task 3: Merge the streams and persist what was earned

**Files:**
- Modify: `backend/miscellaneous.py` (add `merge_balance_events()`)
- Modify: `main.py` (`balances()`)
- Test: `tests/test_achievements.py` (extend)

**Interfaces:**
- Produces: `merge_balance_events(*streams) -> list` — merges event lists chronologically and recomputes `balance` across all of them.
- Produces: `achievements.json`, `{user_id: [{"id", "name", "amount", "earnedAt"}]}`, which only grows.
- Produces: `balanceWithBonuses`, `maxBidWithBonuses` and `eventsWithBonuses` on every entry in `balances.json`.

**Why persist:** the detection looks at the current state. `trades` only grows, so it is safe, but team value is not: drop below 125 Mio and an earned achievement would vanish. The file also gives each achievement a stable date, which the running balance needs for its order. Dates are the moment of first observation — the real date is not derivable, as "Team value bronze" shows: awarded on 06.08. in the feed, invisible in the data we have.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_achievements.py`, before the `__main__` block:

```python
def test_merge_recomputes_the_running_balance():
    transfers = [
        {"date": "2026-08-01T18:00:00+00:00", "type": "start", "amount": 50_000_000,
         "balance": 50_000_000, "playerName": None, "playerImage": None,
         "teamId": None, "tradePartner": None},
        {"date": "2026-08-03T10:00:00Z", "type": "buy", "amount": -2_000_000,
         "balance": 48_000_000, "playerName": "X", "playerImage": None,
         "teamId": "8", "tradePartner": None},
    ]
    bonuses = [
        {"date": "2026-08-02T00:00:00+00:00", "type": "login_bonus", "amount": 10_000,
         "balance": None, "playerName": None, "playerImage": None,
         "teamId": None, "tradePartner": None},
    ]

    merged = miscellaneous.merge_balance_events(transfers, bonuses)

    assert [e["type"] for e in merged] == ["start", "login_bonus", "buy"], \
        f"expected chronological order, got {[e['type'] for e in merged]}"
    assert [e["balance"] for e in merged] == [50_000_000, 50_010_000, 48_010_000], \
        f"expected the balance to be recomputed, got {[e['balance'] for e in merged]}"


def test_merge_leaves_the_inputs_alone():
    transfers = [{"date": "2026-08-01T18:00:00+00:00", "type": "start", "amount": 1,
                  "balance": 1, "playerName": None, "playerImage": None,
                  "teamId": None, "tradePartner": None}]
    bonuses = [{"date": "2026-08-02T00:00:00+00:00", "type": "login_bonus", "amount": 2,
                "balance": None, "playerName": None, "playerImage": None,
                "teamId": None, "tradePartner": None}]

    miscellaneous.merge_balance_events(transfers, bonuses)

    assert transfers[0]["balance"] == 1 and bonuses[0]["balance"] is None, \
        "expected the caller's events to be left alone"


def test_merge_of_nothing_is_empty():
    assert miscellaneous.merge_balance_events([], []) == [], "expected an empty list"
```

And register them in `__main__`:

```python
    print("\nmerge_balance_events()")
    check("recomputes the running balance", test_merge_recomputes_the_running_balance)
    check("leaves the inputs alone", test_merge_leaves_the_inputs_alone)
    check("merging nothing is empty", test_merge_of_nothing_is_empty)
```

- [ ] **Step 2: Run to verify the new checks fail**

Run: `./venv/bin/python tests/test_achievements.py`
Expected: the first 21 still pass, the three new ones error on the missing attribute.

- [ ] **Step 3: Implement the merge**

Add after `detect_achievements()`:

```python
def merge_balance_events(*streams) -> list:
    """### Merge event streams into one chronological list with a running balance.

    The streams come from build_balance_events(), build_login_bonus_events() and the
    achievement events. Each carries its own "amount"; the balance is recomputed across
    the merge, because a bonus between two transfers shifts everything after it.

    Args:
        *streams (list): Event lists, each already in the shape build_balance_events()
            produces.

    Returns:
        list: A new list of new dicts, oldest first, with "balance" filled in.
    """
    merged = sorted(
        (dict(event) for stream in streams for event in stream),
        key=lambda event: parse_feed_timestamp(event["date"]),
    )

    balance = 0
    for event in merged:
        balance += event["amount"]
        event["balance"] = round(balance)

    return merged
```

- [ ] **Step 4: Run to verify all checks pass**

Run: `./venv/bin/python tests/test_achievements.py`
Expected: `24/24 passed`.

- [ ] **Step 5: Wire it into `balances()`**

In `main.py`, add the achievements store next to the other reads, after the league members are loaded:

```python
    ### Achievements earned in earlier runs. The detection looks at the current state, so
    ### without this an achievement would disappear again when the condition stops holding
    ### - team value can fall back below the threshold. It also gives each achievement the
    ### date the running balance needs, since the real one is not derivable.
    achievements_path = path.join(DATA_DIR, "achievements.json")
    earned_before = {}

    if path.exists(achievements_path):
        try:
            with open(achievements_path, "r") as f:
                earned_before = json.load(f)
        except json.JSONDecodeError:
            logging.warning(f"{achievements_path} is empty or invalid. Starting over.")

    ### Turnovers per manager, for the lucky touch family
    turnovers_by_user = {}
    turnovers_path = path.join(DATA_DIR, "turnovers.json")

    if path.exists(turnovers_path):
        try:
            with open(turnovers_path, "r") as f:
                for buy, sell in json.load(f):
                    turnovers_by_user.setdefault(sell["user"], []).append((buy, sell))
        except json.JSONDecodeError:
            logging.warning(f"{turnovers_path} is empty or invalid. No transfer achievements.")

    now = datetime.now(timezone.utc)

    ### The season titles only settle when the last matchday has been played
    season_over = miscellaneous.season_is_over(now)
```

`matchday_points()` and `season_is_over()` are two small readers that belong next to the
catalogue in `backend/miscellaneous.py`:

```python
def matchday_points(performance: dict) -> list:
    """### Read the points a manager scored on each matchday played.

    The performance response nests a list of seasons, each holding its matchdays. Only
    matchdays that have been played carry "mdp".

    Args:
        performance (dict): A /managers/{id}/performance response.

    Returns:
        list: Points per played matchday, current season only.
    """
    seasons = performance.get("it") or []

    if not seasons:
        return []

    ### The current season is the last one in the list
    return [day["mdp"] for day in seasons[-1].get("it", []) if "mdp" in day]


def season_is_over(now: datetime) -> bool:
    """### Whether every matchday of the season has been played.

    Reads match_days.json, which team_value_per_match_day() writes. Without it there is
    no way to tell, and treating the season as running is the safe answer: it only
    withholds the season titles.

    Args:
        now (datetime): The instant to judge against.

    Returns:
        bool: True once the last match has kicked off and finished.
    """
    match_days_path = path.join(DATA_DIR, "match_days.json")

    if not path.exists(match_days_path):
        return False

    try:
        with open(match_days_path, "r") as f:
            match_days = json.load(f)
    except json.JSONDecodeError:
        return False

    if not match_days:
        return False

    return parse_feed_timestamp(match_days[-1]["lastMatch"]) < now
```

Inside the per-user loop, after `balance = events[-1]["balance"]` and before the max bid maths:

```python
        ### Everything below is an estimate. The login bonus assumes a daily login, which
        ### cannot be checked for anyone but the logged in user, and the achievements are
        ### derived from the current state rather than read from the feed.
        bonus_events = miscellaneous.build_login_bonus_events(start_datetime, now)

        ### The team value reward depends on the balance, so judge it against the balance
        ### the manager would have with the login bonuses counted in
        balance_with_bonuses = balance + sum(e["amount"] for e in bonus_events)

        ### Points per matchday, for the point tier achievements. One call per manager.
        matchday_points = miscellaneous.matchday_points(
            leagues.user_performance(user_token, selected_league.id, user_id))

        earned_now = miscellaneous.detect_achievements(
            user_stats.get("t", 0),
            team_value,
            balance_with_bonuses,
            turnovers_by_user.get(user_name, []),
            user_stats["mdw"],
            matchday_points,
            user_stats["pl"],
            season_over,
        )

        ### Keep the date of the first sighting, and never drop one that was earned before
        known = {(a["id"], a["name"]): a for a in earned_before.get(user_id, [])}
        for achievement in earned_now:
            known.setdefault((achievement["id"], achievement["name"]),
                             {**achievement, "earnedAt": now.isoformat()})

        earned_before[user_id] = sorted(known.values(), key=lambda a: a["earnedAt"])

        achievement_events = [{
            "date": a["earnedAt"],
            "type": "achievement",
            "amount": a["amount"],
            "balance": None,
            "achievementName": a["name"],
            "playerName": None,
            "playerImage": None,
            "teamId": None,
            "tradePartner": None,
        } for a in earned_before[user_id]]

        events_with_bonuses = miscellaneous.merge_balance_events(
            events, bonus_events, achievement_events)
        balance_with_bonuses = events_with_bonuses[-1]["balance"]
```

Replace the max bid block so it runs for both figures:

```python
        ### Calculate the maximum allowable bid for both views of the balance
        def max_bid(for_balance):
            """The most the manager could bid, given a balance."""
            adjusted_team_value = team_value + for_balance
            max_negative_balance = adjusted_team_value * 0.33

            if for_balance < 0:
                return max(0, max_negative_balance + for_balance)

            return max(0, max_negative_balance)

        maxbid = max_bid(balance)
```

And extend the written record:

```python
        final_balances.append({
            "userId": user_id,
            "username": user_name,
            "profilePic": miscellaneous.get_profilepic(user_id),
            "teamValue": team_value,
            "balance": balance,
            "maxBid": round(maxbid, 0),
            "events": events,
            "balanceWithBonuses": balance_with_bonuses,
            "maxBidWithBonuses": round(max_bid(balance_with_bonuses), 0),
            "eventsWithBonuses": events_with_bonuses,
        })
```

After the loop, next to the other writes:

```python
    miscellaneous.write_json_to_file(earned_before, "achievements.json")
```

- [ ] **Step 6: Verify against the three known balances**

`balances()` needs live credentials, so check the maths from the committed data instead. Write to the scratchpad and run:

```python
import json, sys, logging
from datetime import datetime, timezone
logging.disable(logging.WARNING)
sys.path.insert(0, ".")
from backend import miscellaneous

st = {u["userName"]: u for u in json.load(open("frontend/src/data/league_user_stats.json"))}
transfers = miscellaneous.drop_reverted_transfers(json.load(open("frontend/src/data/all_transfers.json")))
turnovers = {}
for buy, sell in json.load(open("frontend/src/data/turnovers.json")):
    turnovers.setdefault(sell["user"], []).append((buy, sell))

start = datetime(2026, 8, 1, 18, 0, 0, tzinfo=timezone.utc)
now = datetime(2026, 8, 12, 14, 0, 0, tzinfo=timezone.utc)

EXPECTED = {"shirazzi": -18_346_628, "Twilli": 23_633_259, "Reddy": -18_171_141}

for name, expected in EXPECTED.items():
    events = miscellaneous.build_balance_events(transfers, name, 50_000_000.0, start)
    bonuses = miscellaneous.build_login_bonus_events(start, now)
    with_bonus = events[-1]["balance"] + sum(e["amount"] for e in bonuses)
    earned = miscellaneous.detect_achievements(
        st[name]["trades"], st[name]["teamValue"], with_bonus,
        turnovers.get(name, []), st[name]["mdWins"])
    achievement_events = [{"date": now.isoformat(), "type": "achievement", "amount": a["amount"],
                           "balance": None, "playerName": None, "playerImage": None,
                           "teamId": None, "tradePartner": None} for a in earned]
    merged = miscellaneous.merge_balance_events(events, bonuses, achievement_events)
    got = merged[-1]["balance"]
    print(f"{name:<12}{got:>14,}  expected {expected:>14,}  {'ok' if got == expected else 'MISMATCH'}")
```

Expected: `ok` on all three lines. A mismatch means the model or the wiring is wrong — stop and work out which before going on.

- [ ] **Step 7: Commit**

```bash
git add backend/miscellaneous.py main.py tests/test_achievements.py
git commit -m "feat: fold bonuses and achievements into the balance"
```

---

### Task 4: The switch in the frontend

**Files:**
- Modify: `frontend/src/components/Balances.js`
- Modify: `frontend/src/components/BalanceEventsDialog.js`

**Interfaces:**
- Consumes: `balanceWithBonuses`, `maxBidWithBonuses`, `eventsWithBonuses` from Task 3, and the event types `login_bonus` and `achievement` with `achievementName`.

- [ ] **Step 1: Add the switch and swap the figures in `Balances.js`**

Add the imports:

```jsx
import FormControlLabel from "@mui/material/FormControlLabel"
import Switch from "@mui/material/Switch"
import Box from "@mui/material/Box"
```

Add the state next to `selectedManager`:

```jsx
    // One setting for the table and the dialog, so the two cannot show different
    // assumptions about the same manager
    const [withBonuses, setWithBonuses] = useState(false)
```

The two money columns read whichever figure is selected. Replace the `balance` and `maxBid` column definitions' `field` with a `valueGetter`, leaving everything else as it is:

```jsx
        {
            field: "balance",
            headerName: "Kontostand",
            headerAlign: "center",
            flex: 1,
            align: "center",
            type: "number",
            valueGetter: ({ row }) => withBonuses ? row.balanceWithBonuses : row.balance,
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
        {
            field: "maxBid",
            headerName: "Max. Gebot",
            headerAlign: "center",
            align: "center",
            flex: 1,
            type: "number",
            valueGetter: ({ row }) => withBonuses ? row.maxBidWithBonuses : row.maxBid,
            valueFormatter: ({ value }) => currencyFormatter.format(Number(value)),
        },
```

Carry the new fields into the rows:

```jsx
            balanceWithBonuses: row.balanceWithBonuses,
            maxBidWithBonuses: row.maxBidWithBonuses,
            eventsWithBonuses: row.eventsWithBonuses,
```

And render the switch above the grid:

```jsx
        <>
            <Box sx={{ padding: "0 15px 10px" }}>
                <FormControlLabel
                    control={<Switch checked={withBonuses} onChange={(e) => setWithBonuses(e.target.checked)} />}
                    label="Boni & Erfolge einrechnen (geschätzt)"
                />
            </Box>
            <PagedDataGrid ... />
            <BalanceEventsDialog
                manager={selectedManager}
                withBonuses={withBonuses}
                onClose={() => setSelectedManager(null)}
            />
        </>
```

- [ ] **Step 2: Render the right list and mark the estimates in `BalanceEventsDialog.js`**

Take the new prop and pick the list:

```jsx
function BalanceEventsDialog({ manager, withBonuses, onClose }) {
```

```jsx
    const events = (withBonuses ? manager.eventsWithBonuses : manager.events) || []
```

Extend the labels and add the marking:

```jsx
const eventTypeLabels = {
    start: "Startbudget",
    buy: "Kauf",
    sell: "Verkauf",
    login_bonus: "Login-Bonus",
    achievement: "Erfolg",
}

// Transfers are recorded fact, bonuses and achievements are worked out from the rules.
// Keeping them apart is the point of showing the list at all.
const isEstimate = (type) => type === "login_bonus" || type === "achievement"
```

The "Event" column names the achievement where there is one, and estimates are set in italics:

```jsx
        {
            field: "type",
            headerName: "Event",
            flex: 1,
            minWidth: 120,
            headerAlign: "center",
            align: "center",
            valueGetter: ({ row }) => row.achievementName || eventTypeLabels[row.type] || row.type,
            cellClassName: ({ row }) => isEstimate(row.type) ? "estimated-event" : "",
        },
```

The "Spieler" column already renders nothing without a player, so bonus rows stay empty there. Give the trade partner column the same treatment by leaving it as it is — `tradePartnerLabel` turns `null` into „Kickbase", which is right for both.

Add the styling next to `deltaColumnStyles` on the wrapping `Box`:

```jsx
                <Box sx={{ ...deltaColumnStyles, "& .estimated-event": { fontStyle: "italic", opacity: 0.75 } }}>
```

And say so in the title, so nobody has to infer it:

```jsx
            <DialogTitle>
                Kontostand-Verlauf: {manager.username}
                {withBonuses && (
                    <Typography variant="body2" color="text.secondary">
                        Kursive Zeilen sind geschätzt: täglicher Login unterstellt, Erfolge aus dem Spielstand abgeleitet.
                    </Typography>
                )}
            </DialogTitle>
```

with `import Typography from "@mui/material/Typography"`.

- [ ] **Step 3: Verify it builds**

Run: `cd frontend && npm run build`
Expected: `Compiled successfully.` with no new warnings.

- [ ] **Step 4: Check it in the browser**

The committed `balances.json` has no `eventsWithBonuses` yet, so regenerate it from the repo data first with the script from Task 3 Step 6, extended to write the new fields back into the file.

Start the dev server on a free port (3000 is often taken by another process):

```bash
cd frontend && BROWSER=none PORT=3123 npm start
```

Then check:
1. The switch sits above the table and is off by default.
2. Switching it on raises every Kontostand by at least the login bonus, and Max. Gebot moves with it.
3. Opening a manager with the switch on shows Login-Bonus and Erfolg rows in italics, transfers upright.
4. The last Saldo in the dialog equals the Kontostand in the table, with the switch on and off.
5. Switching off returns the table to the transfer-only figures.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Balances.js frontend/src/components/BalanceEventsDialog.js
git commit -m "feat: switch the balance table between transfers and estimates"
```

---

### Task 5: Say what the numbers mean

**Files:**
- Modify: `frontend/src/App.js:219` (the Balances help text)
- Modify: `README.md`

- [ ] **Step 1: Update the help text**

The current text says the balance is *minus* login bonuses and achievements, which stops being true with the switch on. Replace it:

```jsx
<HelpIcon text="Ungefähre Kontostände der Manager. Mit dem Schalter werden täglicher Login-Bonus und Erfolge eingerechnet - beides geschätzt: der tägliche Login wird unterstellt, die Erfolge aus dem Spielstand abgeleitet. Nicht herleitbare Erfolge fehlen."/>
```

- [ ] **Step 2: Note the new file in the README**

Add `achievements.json` to whatever list of generated data files the README carries. If there is none, skip this step rather than inventing a section.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.js README.md
git commit -m "docs: say what the estimated balance includes"
```

---

## Out of Scope

- **Topscorer, Matchwinner, Weltklasse, Fussballgott, MVP, Tormaschine** — the data does not exist in the project.
- Collapsing the daily bonus rows. 11 rows today, around 280 by the end of the season.

**Added after the plan was written:** the higher tiers of Transfer King and Team value were
unknown at planning time and were read out of the app on 2026-08-12. They are implemented,
but no verified balance crosses one of their thresholds, so they rest on that reading alone.
