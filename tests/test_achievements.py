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

### Make the repository root importable regardless of where this is run from
sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous

### ===============================================================================

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


### ===============================================================================

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


### ===============================================================================

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

    print("\nmerge_balance_events()")
    check("recomputes the running balance", test_merge_recomputes_the_running_balance)
    check("leaves the inputs alone", test_merge_leaves_the_inputs_alone)
    check("merging nothing is empty", test_merge_of_nothing_is_empty)

    total_checks, passed = len(PASSED), sum(PASSED)
    print(f"\n{passed}/{total_checks} passed")
    sys.exit(0 if passed == total_checks else 1)
