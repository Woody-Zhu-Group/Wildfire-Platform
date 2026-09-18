import catalog from '../../shared/dataset_caveats.json' with {type: 'json'};
import type { DatasetId } from './data.ts';

export function datasetCaveats(datasets: readonly DatasetId[]): string[] {
  return [
    ...(datasets.includes('cpuc') ? [catalog.cpuc_utility_caused] : []),
    ...(datasets.includes('calfire') ? [catalog.calfire_map_feed_counts] : []),
    ...(datasets.includes('epss') ? [catalog.epss_pge_only] : []),
    ...(datasets.includes('us_ignitions') ? [catalog.us_ignitions_sample] : []),
  ];
}
