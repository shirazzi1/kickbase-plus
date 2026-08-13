"""Holds the predicted minimum winning bid against the prices that were really paid.

The auction solver in the market table is only worth as much as the balances it stands on,
and those balances are *reconstructions*: the transfer feed plus an assumed daily login
bonus plus derived achievements. A rival estimated too rich inflates every minimum bid; one
estimated too poor produces a phantom badge that invites a losing bid.

This script measures that error against the record. For every purchase in
`all_transfers.json` it rebuilds what every manager's ceiling *would have been* at that
moment, runs the same solver the table runs, and compares:

  - **Deckenverstoß** - the price actually paid against the buyer's reconstructed ceiling.
    A purchase above the ceiling is a bid the model would have called impossible, so it
    falsifies the estimate directly, with a sign and a size.
  - **Aufschlag** - the minimum winning bid the HUD would have suggested against the price
    that actually won. This is what following the advice would have cost extra.

What it cannot measure, stated plainly rather than papered over:

  - **The asking price is gone.** The feed keeps the price paid, never the price asked, so
    the price paid stands in for the floor. Every suggested bid is therefore at least the
    price paid, and the Aufschlag distribution is one sided by construction.
  - **The team value is walked backwards.** Only today's team value is on disk, so earlier
    ones are reconstructed by undoing every transfer since - which ignores market value
    drift in between. `--flat-team-value` runs the same report without that correction, so
    the reader can see how much the choice moves the numbers.
  - **The winning bid is not the necessary bid.** A manager who overpaid by two million
    shows up as an Aufschlag of zero. The Aufschlag is an upper bound on the cost of the
    advice, not the error of the estimate.

    ./venv/bin/python tests/calibrate_min_bid.py
    ./venv/bin/python tests/calibrate_min_bid.py --no-bonuses --flat-team-value
"""

import json
import sys

from datetime import datetime
from os import path

sys.path.insert(0, path.dirname(path.dirname(path.abspath(__file__))))

from backend import miscellaneous
from backend.paths import PUBLIC_DIR, STATE_DIR
from main import max_bid

### ===============================================================================
### Reconstruction
### ===============================================================================


def balance_timeline(manager, with_bonuses=True):
    """Every balance a manager held, oldest first, as (moment, balance) pairs.

    The events already carry the running balance, so this only has to read them in order.
    The bonus view is the one the frontend defaults to.
    """
    events = manager["eventsWithBonuses" if with_bonuses else "events"]

    return sorted(
        (miscellaneous.parse_feed_timestamp(event["date"]), event["balance"])
        for event in events
    )


def balance_at(timeline, when):
    """The balance in force just before `when`, or None if the timeline starts later.

    Strictly before, and that is the whole point: the transfer being judged is itself an
    event on this timeline, so including it would compare the price paid against a balance
    that has already paid it.
    """
    balance = None

    for moment, value in timeline:
        if moment >= when:
            break
        balance = value

    return balance


def team_value_at(manager, when, flat=False):
    """The manager's team value at `when`, walked back from today's along their transfers.

    A buy raises the team value by roughly its price and lowers the balance by it, a sale
    does the reverse - so undoing every transfer since `when` means adding up exactly the
    balance amounts of those events. Market value drift in between is not captured; `flat`
    skips the correction entirely to show what that is worth.
    """
    value = manager["teamValue"]

    if flat:
        return value

    for event in manager["events"]:
        if event["type"] not in ("buy", "sell"):
            continue

        if miscellaneous.parse_feed_timestamp(event["date"]) < when:
            continue

        value += event["amount"]

    return value


def ceilings_at(balances, when, with_bonuses=True, flat_team_value=False):
    """Every manager's reconstructed bidding ceiling at one instant, keyed by user id.

    A manager whose timeline has not started by then is left out rather than guessed at.
    """
    ceilings = {}

    for manager in balances:
        balance = balance_at(balance_timeline(manager, with_bonuses), when)

        if balance is None:
            continue

        ceilings[str(manager["userId"])] = max_bid(
            team_value_at(manager, when, flat_team_value), balance)

    return ceilings


def suggested_bid(price, ceilings, seller_id, buyer_id):
    """The minimum winning bid the market table would have shown the buyer.

    The same rule as `minWinningBid()` in the frontend: the asking price is the floor, and
    one euro over the richest affordable rival beats every bid they could place. The seller
    and the buyer themselves are not rivals.

    Returns (bid, rival_count).
    """
    rivals = sorted(
        ceiling for user_id, ceiling in ceilings.items()
        if user_id not in (seller_id, buyer_id) and ceiling >= price
    )

    if not rivals:
        return price, 0

    return max(price, rivals[-1] + 1), len(rivals)


### ===============================================================================
### The record
### ===============================================================================


def purchases(transfers, name_to_id, start_datetime):
    """Every purchase this season, as (moment, price, buyer id, seller id or None).

    A transfer names the buyer, the seller, or both; the missing side was Kickbase itself.
    Only the ones with an identifiable buyer can be judged, since the buyer's ceiling is
    what the price paid is held against.
    """
    found = []

    for item in miscellaneous.filter_transfers_from(transfers, start_datetime):
        data = item["data"]
        buyer_id = miscellaneous.resolve_user_id(data.get("byr"), name_to_id)

        if buyer_id is None:
            continue

        found.append((
            miscellaneous.parse_feed_timestamp(item["dt"]),
            data["trp"],
            str(buyer_id),
            miscellaneous.resolve_user_id(data.get("slr"), name_to_id),
            data.get("pn"),
        ))

    return sorted(found, key=lambda purchase: purchase[0])


### ===============================================================================
### Output
### ===============================================================================


def percentile(values, share):
    """The value at `share` of the way through a sorted sample, nearest rank."""
    if not values:
        return None

    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))

    return ordered[index]


def euro(value):
    """A euro amount, in the German grouping the frontend uses."""
    if value is None:
        return "–"

    return f"{value:,.0f} €".replace(",", ".")


def share(value):
    """A ratio as a percentage with one decimal."""
    if value is None:
        return "–"

    return f"{value * 100:.1f} %".replace(".", ",")


def distribution(name, values, formatter):
    """One line per quantile of a sample, or a note that the sample is empty."""
    if not values:
        print(f"  {name}: keine Fälle")
        return

    print(f"  {name} (n = {len(values)})")
    for label, value in (("Min", 0.0), ("p25", 0.25), ("Median", 0.5),
                         ("p75", 0.75), ("p90", 0.9), ("Max", 1.0)):
        print(f"    {label:>6}  {formatter(percentile(values, value)):>18}")


def factor(value):
    """A ratio as a multiple, e.g. "6,1×"."""
    if value is None:
        return "–"

    return f"{value:.1f}×".replace(".", ",")


def report(sample_name, cases, manager_count):
    """Print what one sample of purchases says about the estimate and about the model."""
    print(f"\n{sample_name}  (n = {len(cases)})")

    if not cases:
        print("  keine Fälle")
        return

    overruns = [case["price"] - case["buyerCeiling"] for case in cases]
    coverage = [case["buyerCeiling"] / case["price"] for case in cases if case["price"]]
    overshoots = [case["bid"] - case["price"] for case in cases]
    overshoots_relative = [(case["bid"] - case["price"]) / case["price"]
                           for case in cases if case["price"]]
    rivals = [case["rivals"] for case in cases]

    ### 1. Does the reconstructed budget hold at the edge? A purchase above the buyer's
    ###    reconstructed ceiling is a bid the model called impossible, so it falsifies the
    ###    estimate outright - and it is exactly what would produce a false phantom badge.
    violations = [value for value in overruns if value > 0]
    print(f"  Deckenverstöße: {len(violations)} von {len(cases)} "
          f"({share(len(violations) / len(cases))})")

    if violations:
        worst = max((case for case in cases if case["price"] > case["buyerCeiling"]),
                    key=lambda case: (case["price"] - case["buyerCeiling"]) / case["price"])
        print(f"    größter: {euro(max(violations))}, relativ zum Preis am schlimmsten "
              f"{share((worst['price'] - worst['buyerCeiling']) / worst['price'])} "
              f"({worst['player'] or '?'}, {worst['when'].date()})")

    distribution("Deckung des Käufers (Decke ÷ Preis)", coverage, factor)

    ### 2. Does the rival set actually narrow the field? Where every other manager can
    ###    afford the player, the column carries no information beyond "everyone can".
    ###    Everyone but the buyer is a possible rival, and on a manager listing the seller
    ###    drops out too.
    possible = manager_count - 1 - (1 if cases[0]["fromManager"] else 0)
    unrestricted = sum(1 for count in rivals if count >= possible)
    print(f"  Bieterfeld: Median {percentile(rivals, 0.5)} von {possible} möglichen "
          f"Rivalen; bei {share(unrestricted / len(cases))} der Käufe konnten alle mitbieten")
    print(f"    Phantom-Auktionen (kein Rivale konnte zahlen): "
          f"{sum(1 for count in rivals if count == 0)} von {len(cases)}")

    ### 3. What would following the advice have cost? One sided by construction - the price
    ###    paid stands in for the asking price the feed does not keep - so this is an upper
    ###    bound on the cost of the advice, not the error of the estimate.
    distribution("Aufschlag des Mindestgebots", overshoots, euro)
    distribution("dasselbe relativ zum Preis", overshoots_relative, share)


### ===============================================================================


def collect(balances, cases_source, with_bonuses, flat_team_value):
    """Solve every purchase and keep what the report needs."""
    solved = []

    for when, price, buyer_id, seller_id, player in cases_source:
        ceilings = ceilings_at(balances, when, with_bonuses, flat_team_value)

        if buyer_id not in ceilings:
            continue

        bid, rivals = suggested_bid(price, ceilings, str(seller_id) if seller_id else None, buyer_id)

        solved.append({
            "when": when,
            "player": player,
            "price": price,
            "buyerCeiling": ceilings[buyer_id],
            "bid": bid,
            "rivals": rivals,
            "fromManager": seller_id is not None,
        })

    return solved


def main():
    with_bonuses = "--no-bonuses" not in sys.argv
    flat_team_value = "--flat-team-value" in sys.argv

    balances_path = path.join(PUBLIC_DIR, "balances.json")
    transfers_path = path.join(STATE_DIR, "all_transfers.json")
    users_path = path.join(STATE_DIR, "STATIC_users.json")

    missing = [p for p in (balances_path, transfers_path, users_path) if not path.exists(p)]

    if missing:
        print("Die Kalibrierung braucht die Daten eines echten Laufs. Es fehlen:")
        for p in missing:
            print(f"  {p}")
        print("\nEinmal main.py laufen lassen oder die Dateien aus einem Checkout kopieren, "
              "in dem sie liegen.")
        return 0

    with open(balances_path) as f:
        balances = json.load(f)

    with open(transfers_path) as f:
        transfers = json.load(f)

    with open(users_path) as f:
        league_users = json.load(f)

    name_to_id = miscellaneous.build_user_name_index(league_users)

    ### The season start is taken from the balances themselves rather than from START_DATE:
    ### the reconstruction has to use the same cutoff the balances were built with, and a
    ### mismatched environment variable would silently shift every timeline.
    start_datetime = miscellaneous.parse_feed_timestamp(balances[0]["events"][0]["date"])

    cases = collect(balances, purchases(transfers, name_to_id, start_datetime),
                    with_bonuses, flat_team_value)

    view = "mit Boni" if with_bonuses else "nur Transfers"
    team_values = "Teamwert von heute" if flat_team_value else "Teamwert zurückgerechnet"

    print(f"Kalibrierung des Mindestgebots — {len(balances)} Manager, "
          f"{len(transfers)} Feed-Einträge")
    print(f"Budget-Sicht: {view}; {team_values}")
    print(f"Saisonstart: {start_datetime.isoformat()}")

    report("Von Managern gekaufte Spieler",
           [case for case in cases if case["fromManager"]], len(balances))
    report("Von Kickbase gekaufte Spieler",
           [case for case in cases if not case["fromManager"]], len(balances))

    worst = sorted(cases, key=lambda case: case["price"] - case["buyerCeiling"], reverse=True)[:5]

    if worst and worst[0]["price"] > worst[0]["buyerCeiling"]:
        print("\nDie größten Deckenverstöße — hier ist die Schätzung am weitesten daneben:")
        for case in worst:
            if case["price"] <= case["buyerCeiling"]:
                break
            print(f"  {case['when'].date()}  {case['player'] or '?':<18} "
                  f"Preis {euro(case['price']):>16}  Decke {euro(case['buyerCeiling']):>16}  "
                  f"zu niedrig um {euro(case['price'] - case['buyerCeiling'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
