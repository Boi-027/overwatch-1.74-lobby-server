# Runtime data

These are the small data sets required by the server and its tests, not user saves or research logs.

| File | Purpose |
| --- | --- |
| `schemas_174.json` | Layouts of 707 lobby messages in 87 protocol groups. |
| `retail_templates.json` | Decoded reference-server templates, including catalogs and prices; display-name markers are anonymized. |
| `extracted_items.json`, `extracted_general_unlocks.json` | Cosmetic names, types, rarity, categories and rewards. |
| `extracted_heroes.json` | Hero identifiers and names. |
| `extracted_maps.json` | Map metadata and seasonal variants. |
| `resource_keys_174.json` | Game content keys used by event resources. These are not account credentials. |
| `extracted_events_174.json` | Verified scene mappings used by regression tests. |
| `announced_crcs_174.json` | Protocol identifiers used by the schema-extraction tool. |

The raw reference capture was from client 1.68 and was decoded using 1.74 schemas; templates are not a claim that every original live-service feature has been reproduced. The game client, raw captures and process dumps are not distributed here.
