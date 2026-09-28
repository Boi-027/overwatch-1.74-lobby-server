"""Event picker metadata, with scene support separate from event rewards.

Evidence: data/extracted_events_174.json and docs/STATE.md.
Unavailable entries intentionally have no EVENT_PRESETS record. 'verified'
refers to previously reported working scenes, not a new test of these changes.
"""


def _event(id, label, description, category, scene_status='unverified', scene_note=''):
    return dict(id=id, label=label, description=description, category=category,
                scene_status=scene_status, scene_note=scene_note)


EVENT_CATALOG = [
    _event('goodbye', 'Farewell to Overwatch', 'General scene with the original Overwatch heroes.', 'special',
           'verified', 'Worked in previous checks.'),
    _event('lunar', 'Lunar New Year', 'Year of the Tiger: festive maps and loot boxes.', 'seasonal',
           'verified', 'Worked in previous checks.'),
    _event('halloween', 'Halloween Terror', 'Festive Eichenwalde and themed loot boxes.', 'seasonal',
           'unverified', 'The scene exists in the client; switching needs to be verified in game.'),
    _event('winter', 'Winter Wonderland', 'Winter scenes and festive loot boxes.', 'seasonal',
           'verified', 'Worked in previous checks.'),
    _event('anniversary', 'Anniversary: Remix Vol. 3', 'Scene with heroes in anniversary skins.', 'seasonal',
           'verified', 'Scene switching confirmed in game after the update.'),
    _event('anniversary_remix_1', 'Anniversary: Remix Vol. 1', 'Early version of the event with the same anniversary scene.', 'seasonal',
           'verified', 'Scene switching confirmed in game after the update.'),
    _event('anniversary_remix_2', 'Anniversary: Remix Vol. 2', 'Second version of the event with the same anniversary scene.', 'seasonal',
           'verified', 'Scene switching confirmed in game after the update.'),
    _event('summer', 'Summer Games', 'The "Summer Games" event and themed loot boxes.', 'seasonal',
           'limited', 'In this client version the event does not select a separate summer scene.'),
    _event('archives', 'Archives', 'The "Archives" event and themed loot boxes.', 'seasonal',
           'limited', 'In this client version the event does not select a separate "Archives" scene.'),
    _event('reaper', 'Reaper Challenge', 'The "Code of Violence" challenge scene.', 'special',
           'verified', 'Worked in previous checks.'),
    _event('cassidy', 'Cassidy Challenge', 'The "New Blood" challenge scene.', 'special',
           'verified', 'Worked in previous checks.'),
    _event('malevento', 'Malevento', 'Main menu with a Malevento view.', 'special',
           'verified', 'Worked in previous checks.'),
    _event('owl', 'Overwatch League: Genji', 'Overwatch League scene with Genji.', 'esports',
           'verified', 'Worked in previous checks.'),
    _event('contenders', 'Overwatch Contenders', 'Esports event with login rewards.', 'esports',
           'limited', 'Rewards are available, but no separate scene is assigned to this event in the client.'),
    _event('tracer_comic', 'Tracer: Comic', 'Historical Tracer scene with a comic panel.', 'special',
           'unavailable', 'In version 1.74 the former scene is replaced by the Reaper scene. The original cannot be enabled through the event settings.'),
]
