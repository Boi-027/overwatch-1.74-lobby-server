"""Building full teams from solo players and parties, and the queue cards' rules."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game.content import game_map
from ow174.lobby.matchmaker import QUICK_PLAY, Matchmaker, Ruleset, Ticket, build_roster, load_rules

TANK, DAMAGE, SUPPORT = 2, 1, 3
SIX = Ruleset()
TWO_OF_EACH = {TANK: 2, DAMAGE: 2, SUPPORT: 2}
KINGS_ROW, HYBRID, DEATHMATCH = 0x08000000000000D4, 0x0230000000000016, 0x023000000000001E


def tickets(*parties):
    """One ticket per party; a party is a list of the members' picked roles ([] = any)."""
    made = []
    for number, members in enumerate(parties):
        party = SimpleNamespace(name=f"party {number}")
        accounts = [(SimpleNamespace(name=f"p{number}.{k}"), roles) for k, roles in enumerate(members)]
        made.append(Ticket(party, {}, 1, number, accounts))
    return made


def sizes(roster):
    return [len(team) for team in roster.teams]


class RosterTests(unittest.TestCase):
    def test_solo_players_and_parties_fill_both_teams(self):
        roster = build_roster(tickets([[]] * 4, [[]] * 2, [[]], [[]] * 3, [[]] * 2), SIX, {})
        self.assertEqual(sizes(roster), [6, 6])

    def test_nobody_plays_until_the_teams_are_full(self):
        self.assertIsNone(build_roster(tickets([[]] * 5, [[]] * 6), SIX, {}))

    def test_a_party_is_never_split(self):
        roster = build_roster(tickets([[]] * 4, [[]] * 4, [[]] * 2, [[]] * 2), SIX, {})
        for team in roster.teams:
            for ticket in {id(entry[0]): entry[0] for entry in team}.values():
                self.assertEqual(sum(1 for entry in team if entry[0] is ticket), len(ticket.members))

    def test_a_role_queue_needs_two_of_each_role(self):
        self.assertIsNone(build_roster(tickets(*[[[DAMAGE]]] * 12), SIX, TWO_OF_EACH))
        mixed = tickets(*([[[TANK]]] * 4 + [[[DAMAGE]]] * 4 + [[[SUPPORT]]] * 4))
        for team in build_roster(mixed, SIX, TWO_OF_EACH).teams:
            self.assertEqual(
                sorted(role for _, _, role in team), [DAMAGE, DAMAGE, TANK, TANK, SUPPORT, SUPPORT]
            )

    def test_flex_players_take_the_role_that_is_missing(self):
        parties = [[[TANK]]] * 2 + [[[DAMAGE]]] * 4 + [[[SUPPORT]]] * 4 + [[[TANK, DAMAGE, SUPPORT]]] * 2
        self.assertEqual(sizes(build_roster(tickets(*parties), SIX, TWO_OF_EACH)), [6, 6])

    def test_three_against_three_and_one_against_five(self):
        roster = build_roster(tickets([[]] * 2, [[]], [[]] * 3), Ruleset(team_sizes=(3, 3)), {})
        self.assertEqual(sizes(roster), [3, 3])
        yeti = build_roster(tickets([[]] * 5, [[]]), Ruleset(team_sizes=(1, 5)), {})
        self.assertEqual(sizes(yeti), [1, 5])

    def test_free_for_all(self):
        rules = Ruleset(team_sizes=(8,), free_for_all=True)
        self.assertIsNone(build_roster(tickets(*[[[]]] * 7), rules, {}))
        roster = build_roster(tickets(*[[[]]] * 9), rules, {})
        self.assertEqual(sum(sizes(roster)), 8)
        self.assertTrue(all(size == 1 for size in sizes(roster)))

    def test_a_test_minimum_starts_with_whoever_waits(self):
        self.assertEqual(sum(sizes(build_roster(tickets([[]]), SIX, TWO_OF_EACH, minimum=1))), 1)
        self.assertEqual(sizes(build_roster(tickets([[]], [[]]), SIX, {}, minimum=2)), [1, 1])


class RulesTests(unittest.TestCase):
    def test_quick_play_is_six_against_six_with_two_of_each_role(self):
        rules = load_rules()[QUICK_PLAY]
        self.assertEqual({ruleset.team_sizes for ruleset in rules.rulesets}, {(6, 6)})
        self.assertEqual(rules.roles, TWO_OF_EACH)
        self.assertTrue(rules.maps)

    def test_every_quick_play_map_can_be_hosted(self):
        rules = load_rules()[QUICK_PLAY]
        choices = [
            game_map(guid, ruleset.mode)
            for guid, modes in rules.maps
            for ruleset in rules.rulesets
            if ruleset.mode in modes
        ]
        self.assertTrue(all(choices), "a Quick Play map without placeables or spawns")

    def test_a_map_in_a_mode_has_that_modes_placeables_and_both_teams_spawns(self):
        hybrid = game_map(KINGS_ROW, HYBRID)
        self.assertTrue(hybrid.placeables)
        self.assertEqual({spawn.team for spawn in hybrid.spawns}, {0, 1})
        self.assertIsNone(game_map(KINGS_ROW, 0x0230000000000008))  # its loading needs a mode script

    def test_a_search_picks_a_map_of_the_card(self):
        server = SimpleNamespace(game=None)
        matchmaker = Matchmaker(server)
        chosen, ruleset = matchmaker._pick_map(matchmaker.rules_of(QUICK_PLAY))
        self.assertIn(chosen.mode_guid, {r.mode for r in matchmaker.rules_of(QUICK_PLAY).rulesets})
        self.assertEqual(chosen.mode_guid, ruleset.mode)


if __name__ == "__main__":
    unittest.main()
