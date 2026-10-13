// Example questions under the Ask box. Each one is, word for word, an eval case
// the router answers deterministically (services/agent/eval/cases.json,
// expected_status "answer", expected_route "deterministic"), so a hint never
// sends someone down an untested route. tests/ask-hints.test.ts enforces that.
// tests/measured-coverage.test.ts keeps website source free of hardcoded
// utility names, so hints avoid the ampersand spelling of the largest utility.
export const ASK_HINTS: readonly string[] = [
  'How many SCE utility-attributed ignitions were there in 2023?',
  'Compare PGE versus SCE ignition counts in 2024.',
  'Map EPSS outages for 2024.',
  'Show the monthly CAL FIRE incident trend for 2024.',
  'Which counties had the most CAL FIRE incidents in 2023?',
  'List PGE PSPS events in 2021.',
  'Show customers affected over time in 2024.',
  'Which IOU, HFTD tier, and grid cell contain 38.58,-121.49?',
  'What was fitted ignition risk in Sacramento County on 2024-08-15?',
  'Show the modeled ignition risk surface for Sacramento County on 2024-08-15.',
];

/** How many hints show before "Show more". */
export const ASK_HINTS_SHOWN = 3;
