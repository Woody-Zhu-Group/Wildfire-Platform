import { asNumber, type EventRecord } from './data.ts';

export interface ExposureMetric {
  id: 'outages' | 'medical_baseline' | 'life_support';
  label: string;
  // null when every outage lacks the value: not available, never 0.
  value: number | null;
  missing: number;
  unit: 'events' | 'customer-events';
}

export function medicalExposureMetrics(events: readonly EventRecord[]): ExposureMetric[] {
  const sum = (field: 'medical_baseline' | 'life_support') => {
    const values = events.map(event => asNumber(event.properties[field]));
    const known = values.filter((value): value is number => value !== null);
    return {
      // No outages is a true zero; outages that all lack the value are not.
      value: values.length && !known.length ? null : known.reduce((total, value) => total + value, 0),
      missing: values.length - known.length,
    };
  };
  const medical = sum('medical_baseline');
  const lifeSupport = sum('life_support');
  return [
    {id: 'outages', label: 'EPSS outages', value: events.length, missing: 0, unit: 'events'},
    {id: 'medical_baseline', label: 'Medical baseline customer-event total', ...medical, unit: 'customer-events'},
    {id: 'life_support', label: 'Life support customer-event total', ...lifeSupport, unit: 'customer-events'},
  ];
}
